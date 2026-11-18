"""Real commit/rollback, waiting locks and CLI; clean only exact owned IDs."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
import os
import subprocess
import sys
from threading import Event, Lock

import mysql.connector
import pytest

from allocation_helpers import execute
from app.allocation import AllocationError, AllocationConflict, InsufficientResources
from app.db import PROJECT_ROOT, connect_database
from app.proposal import ProposalConflict
from app.services.allocations import AllocationService
from app.services.proposals import ProposalService
from mysql_audit import snapshot


pytestmark = pytest.mark.mysql


@pytest.fixture
def live(mysql_connection):
    mysql_connection.rollback()
    allocations, proposals, guard = set(), set(), Lock()
    with closing(connect_database('TEST_MYSQL_')) as admin:
        assert execute(admin, 'SELECT GET_LOCK(CONCAT(DATABASE(), %s), 0)', ('_v022_seed_tests',)) == [(1,)]
        before = snapshot(admin, 'v0.3-2')
        assert execute(admin, 'SELECT allocation_id FROM allocations') == []
        assert execute(admin, 'SELECT proposal_id FROM job_proposals') == []
        admin.rollback()

        def track(record):
            with guard:
                allocations.add(record.allocation_id)
            return record

        def generate(job):
            with closing(connect_database('TEST_MYSQL_')) as cn:
                generated = ProposalService(cn).generate(job)
            with guard:
                proposals.update(p.proposal_id for p in generated.proposals)
            return generated

        def setup(job=3, kind='DISTRIBUTED', owner=2):
            generated = generate(job)
            with closing(connect_database('TEST_MYSQL_')) as cn:
                proposal = next(p for p in generated.proposals if p.proposal_type == kind)
                response = ProposalService(cn).respond(job, proposal.proposal_id, owner, 'ACCEPTED')
                return proposal, response

        try:
            yield dict(admin=admin, track=track, setup=setup, generate=generate)
        finally:
            admin.rollback()
            for identifier in sorted(allocations):
                for table in ('allocation_bases', 'allocation_gpus', 'allocations'):
                    execute(admin, f'DELETE FROM {table} WHERE allocation_id=%s', (identifier,))
            for identifier in sorted(proposals):
                execute(admin, 'DELETE FROM proposal_responses WHERE proposal_id=%s', (identifier,))
                execute(admin, 'DELETE FROM job_proposals WHERE proposal_id=%s', (identifier,))
            admin.commit()
            assert snapshot(admin, 'v0.3-2') == before
            admin.rollback()
            execute(admin, 'SELECT RELEASE_LOCK(CONCAT(DATABASE(), %s))', ('_v022_seed_tests',))


def race(live, first_action, second_action):
    locked, attempt, acquired, release = (Event() for _ in range(4))

    def worker(first):
        with closing(connect_database('TEST_MYSQL_')) as cn:
            execute(cn, 'SET SESSION innodb_lock_wait_timeout=5')
            service = AllocationService(cn)
            if first:
                original = service.locked_context

                def pause(job):
                    context = original(job)
                    locked.set()
                    assert release.wait(8)
                    return context

                service.locked_context = pause
            else:
                original = service.repository.lock_request

                def observe(requirement):
                    attempt.set()
                    original(requirement)
                    acquired.set()

                service.repository.lock_request = observe
            try:
                result = (first_action if first else second_action)(service)
                return live['track'](result)
            except AllocationError as error:
                return error

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(worker, True)
        try:
            assert locked.wait(5)
            second = pool.submit(worker, False)
            assert attempt.wait(5)
            assert not acquired.wait(0.2), 'Second connection bypassed model lock'
        finally:
            release.set()
        results = first.result(timeout=12), second.result(timeout=12)
    assert acquired.is_set()
    return results


@pytest.mark.parametrize('second', ['same', 'original', 'competing-job'])
def test_real_concurrent_accepted_allocation_is_serialized(live, second):
    proposal, _ = live['setup']()
    action = lambda service: service.allocate_proposal(3, proposal.proposal_id, 2)
    follower = action if second == 'same' else lambda service: service.allocate(3 if second == 'original' else 1)
    first, follow = race(live, action, follower)
    assert len(first.gpus) == 4
    if second == 'same':
        assert first.allocation_id == follow.allocation_id and follow.replayed
    else:
        assert isinstance(follow, AllocationConflict if second == 'original' else InsufficientResources)
    assert execute(live['admin'], 'SELECT COUNT(*) FROM allocations') == [(1,)]
    assert execute(live['admin'], 'SELECT COUNT(*) FROM allocation_bases') == [(1,)]
    assert execute(live['admin'], 'SELECT COUNT(*) FROM allocation_gpus WHERE released_at IS NULL') == [(4,)]


def test_concurrent_finish_returns_once_and_preserves_evidence(live):
    proposal, _ = live['setup']()
    with closing(connect_database('TEST_MYSQL_')) as cn:
        initial = live['track'](AllocationService(cn).allocate_proposal(3, proposal.proposal_id, 2))
    action = lambda service: service.finish(initial.allocation_id)
    first, second = race(live, action, action)
    assert first.ended_at == second.ended_at and second.replayed
    assert first.basis == second.basis == initial.basis
    assert all(g.released_at == first.ended_at for g in first.gpus)


def test_real_duplicate_job_and_gpu_constraints(live):
    proposal, _ = live['setup']()
    with closing(connect_database('TEST_MYSQL_')) as cn:
        record = live['track'](AllocationService(cn).allocate_proposal(3, proposal.proposal_id, 2))
        with pytest.raises(mysql.connector.Error) as caught:
            execute(cn, 'INSERT INTO allocations (job_request_id) VALUES (3)')
        assert caught.value.errno == 1062
        cn.rollback()
        other = execute(cn, 'INSERT INTO allocations (job_request_id) VALUES (1)')
        with pytest.raises(mysql.connector.Error) as caught:
            execute(cn, 'INSERT INTO allocation_gpus (allocation_id,gpu_id,server_id) VALUES (%s,%s,%s)',
                    (other, record.gpus[0].gpu_id, record.gpus[0].server_id))
        assert caught.value.errno == 1062
        cn.rollback()


def test_actual_transaction_rolls_back_gpu_and_header_on_basis_failure(live, monkeypatch):
    proposal, _ = live['setup']()
    before = snapshot(live['admin'], 'v0.3-2')
    with closing(connect_database('TEST_MYSQL_')) as cn:
        service = AllocationService(cn)

        def fail(*args):
            raise RuntimeError('injected after GPU INSERTs')

        monkeypatch.setattr(service.repository, 'create_basis', fail)
        with pytest.raises(RuntimeError):
            service.allocate_proposal(3, proposal.proposal_id, 2)
        assert not cn.in_transaction
    live['admin'].rollback()
    assert snapshot(live['admin'], 'v0.3-2') == before


def cli(*args):
    env = os.environ.copy()
    env.update(PYTHONDONTWRITEBYTECODE='1', PYTHONIOENCODING='utf-8')
    result = subprocess.run([sys.executable, '-B', '-m', 'app.allocation_cli', '--test', *map(str, args)],
                            cwd=PROJECT_ROOT, env=env, capture_output=True, encoding='utf-8', timeout=20)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_live_cli_allocate_evidence_gpu_history_and_return(live):
    proposal, response = live['setup']()
    result = cli('allocate-proposal', 3, '--proposal-id', proposal.proposal_id, '--customer-id', 2)
    # Register the exact committed ID before subsequent assertions/subprocesses.
    class Record:
        allocation_id = result['allocation_id']
    live['track'](Record())
    identifier = result['allocation_id']
    assert result['basis']['response_id'] == response.response_id
    assert len(result['gpus']) == 4
    assert cli('detail', identifier)['basis'] == result['basis']
    assert cli('job-history', 3)['history'][0]['allocation_id'] == identifier
    assert cli('history', result['gpus'][0]['gpu_id'])['history'][0]['allocation_id'] == identifier
    assert cli('complete', identifier)['status'] == 'COMPLETED'
    assert cli('detail', identifier)['ended_at'] is not None


def test_acceptance_waiting_behind_original_allocation_is_refused_after_commit(live):
    """F1 race: respond() waits on the model/Job locks held by allocate(); once the
    original allocation commits, the new ACCEPTED response must be refused."""
    with closing(connect_database('TEST_MYSQL_')) as cn:
        blocker = live['track'](AllocationService(cn).allocate(1))     # H100 GPUs 1, 2
    (proposal,) = live['generate'](2).proposals                       # Job 2 reduced to 3 GPUs
    assert proposal.proposal_type == 'REDUCED_QUANTITY' and proposal.gpu_count == 3
    with closing(connect_database('TEST_MYSQL_')) as cn:
        AllocationService(cn).finish(blocker.allocation_id)
    locked, attempt, acquired, release = (Event() for _ in range(4))

    def allocate_original():
        with closing(connect_database('TEST_MYSQL_')) as cn:
            execute(cn, 'SET SESSION innodb_lock_wait_timeout=5')
            service = AllocationService(cn)
            original = service.locked_context

            def pause(job):
                context = original(job)
                locked.set()
                assert release.wait(8)
                return context

            service.locked_context = pause
            return live['track'](service.allocate(2))

    def accept_stale_proposal():
        with closing(connect_database('TEST_MYSQL_')) as cn:
            execute(cn, 'SET SESSION innodb_lock_wait_timeout=5')
            service = ProposalService(cn)
            original = service.repository.lock_request

            def observe(requirement):
                attempt.set()
                original(requirement)
                acquired.set()

            service.repository.lock_request = observe
            try:
                return service.respond(2, proposal.proposal_id, 1, 'ACCEPTED')
            except ProposalConflict as error:
                return error

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(allocate_original)
        try:
            assert locked.wait(5)
            second = pool.submit(accept_stale_proposal)
            assert attempt.wait(5)
            assert not acquired.wait(0.2), 'respond() bypassed the model/Job lock held by allocate()'
        finally:
            release.set()
        allocated, refused = first.result(timeout=12), second.result(timeout=12)
    assert acquired.is_set()
    assert allocated.status == 'ACTIVE' and len(allocated.gpus) == 4
    assert isinstance(refused, ProposalConflict) and '배정 이력' in str(refused)
    assert execute(live['admin'], 'SELECT COUNT(*) FROM proposal_responses') == [(0,)]
    assert execute(live['admin'], 'SELECT COUNT(*) FROM allocations WHERE job_request_id = 2') == [(1,)]
