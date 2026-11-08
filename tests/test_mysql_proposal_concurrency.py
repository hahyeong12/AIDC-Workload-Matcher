"""Live transactions/CLI; clean up exact owned IDs and preserve all old data."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
import os
from pathlib import Path
import subprocess
import sys
from threading import Event

import mysql.connector
import pytest

from allocation_helpers import execute
from app.db import DatabaseOperationError, connect_database
from app.proposal import ProposalConflict, ProposalError
from app.services.proposals import ProposalService
from mysql_audit import snapshot
from proposal_helpers import live_proposals


pytestmark = pytest.mark.mysql


def race(fixture, first_action, second_action):
    locked, attempt, acquired, release = (Event() for _ in range(4))
    def first_worker():
        with closing(connect_database("TEST_MYSQL_")) as cn:
            execute(cn, "SET SESSION innodb_lock_wait_timeout=5")
            service = ProposalService(cn)
            original = service.locked_job
            def pause(job):
                context = original(job)
                locked.set()
                assert release.wait(5)
                return context
            service.locked_job = pause
            return first_action(service)
    def second_worker():
        with closing(connect_database("TEST_MYSQL_")) as cn:
            execute(cn, "SET SESSION innodb_lock_wait_timeout=5")
            service = ProposalService(cn)
            original = service.repository.lock_request
            def observe(requirement):
                attempt.set()
                original(requirement)
                acquired.set()
            service.repository.lock_request = observe
            try:
                return second_action(service)
            except ProposalError as error:
                return error
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(first_worker)
        try:
            assert locked.wait(3)
            second = executor.submit(second_worker)
            assert attempt.wait(3)
            assert not acquired.wait(0.2), "Follower bypassed the model/Job lock"
        finally:
            release.set()
        result = first.result(timeout=8), second.result(timeout=8)
    assert acquired.is_set()
    return result


@pytest.mark.parametrize("decision", ["ACCEPTED", "REJECTED"])
def test_same_proposal_concurrent_repeats_commit_one_response(live_proposals, decision):
    fixture = live_proposals
    proposal = fixture["generate"](3).proposals[0]
    first, second = race(fixture,
        lambda service: service.respond(3, proposal.proposal_id, 2, decision),
        lambda service: service.respond(3, proposal.proposal_id, 2, decision))
    assert first.response_id == second.response_id and first.responded_at == second.responded_at
    assert second.replayed
    assert execute(fixture["admin"], "SELECT COUNT(*) FROM proposal_responses WHERE job_request_id=3") == [(1,)]


def test_conflicting_concurrent_response_preserves_first_terminal_decision(live_proposals):
    proposal = live_proposals["generate"](3).proposals[0]
    first, second = race(live_proposals,
        lambda service: service.respond(3, proposal.proposal_id, 2, "REJECTED"),
        lambda service: service.respond(3, proposal.proposal_id, 2, "ACCEPTED"))
    assert first.decision == "REJECTED" and isinstance(second, ProposalConflict)
    assert execute(live_proposals["admin"], "SELECT decision FROM proposal_responses WHERE job_request_id=3") == [("REJECTED",)]


def test_concurrent_acceptances_for_two_proposals_commit_only_one(live_proposals):
    first_proposal, second_proposal = live_proposals["generate"](3).proposals
    first, second = race(live_proposals,
        lambda service: service.respond(3, first_proposal.proposal_id, 2, "ACCEPTED"),
        lambda service: service.respond(3, second_proposal.proposal_id, 2, "ACCEPTED"))
    assert first.proposal_id == first_proposal.proposal_id and isinstance(second, ProposalConflict)
    assert execute(live_proposals["admin"], "SELECT COUNT(*) FROM proposal_responses WHERE job_request_id=3 AND decision='ACCEPTED'") == [(1,)]


def test_concurrent_generation_deduplicates_conditions_without_overwriting(live_proposals):
    first, second = race(live_proposals,
        lambda service: live_proposals["track"](service.generate(3)),
        lambda service: live_proposals["track"](service.generate(3)))
    assert [(p.proposal_id, p.created_at) for p in first.proposals] == [(p.proposal_id, p.created_at) for p in second.proposals]
    assert all(p.replayed for p in second.proposals)
    assert execute(live_proposals["admin"], "SELECT COUNT(*) FROM job_proposals WHERE job_request_id=3") == [(2,)]


def test_database_accepted_job_unique_blocks_direct_concurrent_writers(live_proposals):
    first, second = live_proposals["generate"](3).proposals
    inserted, attempt, finished, release = (Event() for _ in range(4))
    sql = "INSERT INTO proposal_responses (proposal_id,job_request_id,customer_id,decision) VALUES (%s,3,2,'ACCEPTED')"
    def leader():
        with closing(connect_database("TEST_MYSQL_")) as cn:
            try:
                cn.start_transaction(isolation_level="READ COMMITTED")
                execute(cn, sql, (first.proposal_id,))
                inserted.set()
                assert release.wait(5)
                cn.commit()
            finally:
                cn.rollback()
    def follower():
        with closing(connect_database("TEST_MYSQL_")) as cn:
            execute(cn, "SET SESSION innodb_lock_wait_timeout=5")
            try:
                attempt.set()
                execute(cn, sql, (second.proposal_id,))
                raise AssertionError("Second acceptance unexpectedly succeeded")
            except mysql.connector.Error as error:
                return error.errno
            finally:
                cn.rollback()
                finished.set()
    with ThreadPoolExecutor(max_workers=2) as executor:
        a = executor.submit(leader)
        try:
            assert inserted.wait(3)
            b = executor.submit(follower)
            assert attempt.wait(3)
            assert not finished.wait(0.2)
        finally:
            release.set()
        a.result(timeout=8)
        assert b.result(timeout=8) == 1062
    assert execute(live_proposals["admin"], "SELECT proposal_id FROM proposal_responses WHERE job_request_id=3") == [(first.proposal_id,)]


def test_real_outer_response_transaction_rolls_back_after_insert(live_proposals, monkeypatch):
    first, second = live_proposals["generate"](3).proposals
    before = snapshot(live_proposals["admin"], "v0.3-1")
    live_proposals["admin"].rollback()
    with closing(connect_database("TEST_MYSQL_")) as cn:
        service = ProposalService(cn)
        create = service.repository.create_response
        def fail(*args):
            result = create(*args)
            service.repository.query("INSERT INTO proposal_responses (proposal_id,job_request_id,customer_id,decision) "
                                     "VALUES (%s,3,2,'INVALID')", (second.proposal_id,), write=True)
            return result
        monkeypatch.setattr(service.repository, "create_response", fail)
        with pytest.raises(DatabaseOperationError) as error:
            service.respond(3, first.proposal_id, 2, "ACCEPTED")
        assert error.value.errno == 3819
        assert not cn.in_transaction
    assert snapshot(live_proposals["admin"], "v0.3-1") == before


def test_real_outer_generation_rolls_back_first_insert_when_second_fails(live_proposals, monkeypatch):
    before = snapshot(live_proposals["admin"], "v0.3-1")
    live_proposals["admin"].rollback()
    with closing(connect_database("TEST_MYSQL_")) as cn:
        service = ProposalService(cn)
        create = service.repository.create_proposal
        calls = []
        def fail(job, customer, draft):
            calls.append(draft)
            if len(calls) == 2:
                return create(18446744073709551615, customer, draft)
            identifier = create(job, customer, draft)
            live_proposals["track"]([identifier])
            return identifier
        monkeypatch.setattr(service.repository, "create_proposal", fail)
        with pytest.raises(DatabaseOperationError): service.generate(3)
        assert len(calls) == 2 and not cn.in_transaction
    assert snapshot(live_proposals["admin"], "v0.3-1") == before


def test_real_cli_generation_details_acceptance_rejection_and_history(live_proposals):
    original = snapshot(live_proposals["admin"], "v0.2-2")
    live_proposals["admin"].rollback()
    def cli(*args, expected=0):
        run = subprocess.run([sys.executable, "-m", "app.proposal_cli", "--test", *map(str, args)],
                             capture_output=True, encoding="utf-8", timeout=10,
                             env=dict(os.environ, PYTHONIOENCODING="utf-8"))
        assert run.returncode == expected, run.stderr
        result = json.loads(run.stdout if expected == 0 else run.stderr)
        if args[0] == "generate" and expected == 0:
            live_proposals["track"]([p["proposal_id"] for p in result["proposals"]])
        return result

    h100 = cli("generate", "003")
    first, second = h100["proposals"]
    assert [(p["gpu_count"], p["same_server_required"]) for p in (first, second)] == [(4, False), (3, True)]
    assert cli("generate", 3)["proposals"][0]["replayed"]
    assert len(cli("list", 3)["proposals"]) == 2
    assert cli("show", first["proposal_id"])["original_requirement"]["same_server_required"]
    assert cli("accept", 3, first["proposal_id"], 3, expected=2)["error"] == "CUSTOMER_MISMATCH"
    rejected = cli("reject", 3, second["proposal_id"], 2)
    assert rejected["decision"] == "REJECTED" and cli("reject", 3, second["proposal_id"], 2)["replayed"]
    accepted = cli("accept", 3, first["proposal_id"], 2)
    replay = cli("accept", 3, first["proposal_id"], 2)
    assert accepted["decision"] == "ACCEPTED" and accepted["responded_at"] == replay["responded_at"] and replay["replayed"]
    assert cli("reject", 3, first["proposal_id"], 2, expected=2)["error"] == "PROPOSAL_CONFLICT"
    assert [r["decision"] for r in cli("history", 3)["responses"]] == ["REJECTED", "ACCEPTED"]
    a100 = cli("generate", "005")
    only, = a100["proposals"]
    assert only["gpu_count"] == 2 and not only["same_server_required"]
    assert cli("reject", 5, only["proposal_id"], 3)["decision"] == "REJECTED"
    assert "실제 배정 성공을 의미하지" in accepted["scope"] and "인증이 아닙니다" in accepted["authentication_notice"]
    assert snapshot(live_proposals["admin"], "v0.2-2") == original
    evidence = {'job003': h100, 'job005': a100, 'accepted': accepted, 'rejected': rejected,
                'originals_and_gpu_allocations_unchanged': True}
    (Path('.pytest-tmp') / 'v031-cli-evidence.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8')
    print('Real CLI: Job003 -> H100 4 distributed / H100 3 same-server; Job005 -> A100 2 distributed-allowed; accept/reject recorded; allocations unchanged')
