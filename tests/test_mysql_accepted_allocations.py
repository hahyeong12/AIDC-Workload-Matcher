"""Real MySQL accepted allocations; private work is entirely rolled back."""

from dataclasses import replace

import mysql.connector
import pytest

from allocation_helpers import RollbackEnvelope, execute
from app.allocation import AllocationConflict, AllocationIntegrityError, InsufficientResources
from app.proposal import ProposalConflict
from app.services.allocations import AllocationService, choose_gpus, get_availability
from app.services.proposals import ProposalService
from mysql_audit import snapshot
from proposal_helpers import proposal_case


pytestmark = pytest.mark.mysql


def accept(db, job=3, kind='DISTRIBUTED', owner=2):
    proposals = ProposalService(RollbackEnvelope(db))
    proposal = next(p for p in proposals.generate(job).proposals if p.proposal_type == kind)
    response = proposals.respond(job, proposal.proposal_id, owner, 'ACCEPTED')
    return proposal, response


@pytest.mark.parametrize('job,kind,owner,ids,same', [
    (3, 'DISTRIBUTED', 2, (1, 2, 3, 5), False),
    (3, 'REDUCED_QUANTITY', 2, (1, 2, 3), True),
    (5, 'REDUCED_QUANTITY', 3, (7, 8), False),
])
def test_seed_alternative_actual_gpus_basis_return_and_history(db, job, kind, owner, ids, same):
    before = snapshot(db, 'v0.3-2')
    proposal, response = accept(db, job, kind, owner)
    service = AllocationService(RollbackEnvelope(db))
    record = service.allocate_proposal(job, proposal.proposal_id, owner)
    assert tuple(g.gpu_id for g in record.gpus) == ids
    assert record.basis.basis_type == 'ACCEPTED_PROPOSAL'
    assert record.basis.response_id == response.response_id
    assert record.basis.proposal_id == proposal.proposal_id
    assert record.basis.gpu_count == len(ids) and record.basis.same_server_required == same
    assert service.allocate_proposal(job, proposal.proposal_id, owner).replayed
    final = service.finish(record.allocation_id)
    assert final.status == 'COMPLETED' and final.basis == record.basis
    assert all(g.released_at == final.ended_at for g in final.gpus)
    assert service.finish(record.allocation_id).replayed
    assert service.allocate_proposal(job, proposal.proposal_id, owner).allocation_id == record.allocation_id
    assert service.detail(record.allocation_id) == final
    assert service.repository.history_for_job(job) == (final,)
    # Reuse returned GPUs for a distinct original Job; the accepted history remains.
    original_job = 1 if job == 3 else 4
    reused = service.allocate(original_job)
    assert set(g.gpu_id for g in reused.gpus) <= set(ids)
    assert reused.basis.basis_type == 'ORIGINAL'
    for table in ('job_requests', 'job_requirements', 'gpus', 'gpu_models', 'servers', 'customers'):
        assert snapshot(db, 'v0.3-2')[table] == before[table]


@pytest.mark.parametrize('decision', [None, 'REJECTED'])
def test_missing_or_rejected_consent_cannot_allocate(db, decision):
    proposals = ProposalService(RollbackEnvelope(db))
    proposal = proposals.generate(3).proposals[0]
    if decision:
        proposals.respond(3, proposal.proposal_id, 2, decision)
    before = snapshot(db, 'v0.3-2')
    with pytest.raises(AllocationConflict):
        AllocationService(RollbackEnvelope(db)).allocate_proposal(3, proposal.proposal_id, 2)
    assert snapshot(db, 'v0.3-2') == before


@pytest.mark.parametrize('job,customer', [(3, 1), (5, 2)])
def test_wrong_customer_or_other_jobs_proposal_is_rejected(db, job, customer):
    proposal, _ = accept(db)
    before = snapshot(db, 'v0.3-2')
    with pytest.raises(AllocationConflict):
        AllocationService(RollbackEnvelope(db)).allocate_proposal(job, proposal.proposal_id, customer)
    assert snapshot(db, 'v0.3-2') == before


def test_resources_are_rechecked_after_acceptance_no_reservation(db):
    before = snapshot(db, 'v0.2-2')
    proposal, _ = accept(db)
    assert snapshot(db, 'v0.2-2') == before
    service = AllocationService(RollbackEnvelope(db))
    competitor = service.allocate(1)
    before_attempt = snapshot(db, 'v0.3-2')
    with pytest.raises(InsufficientResources):
        service.allocate_proposal(3, proposal.proposal_id, 2)
    assert snapshot(db, 'v0.3-2') == before_attempt
    service.finish(competitor.allocation_id)
    assert len(service.allocate_proposal(3, proposal.proposal_id, 2).gpus) == 4


@pytest.mark.parametrize('stage', ['second-gpu', 'basis', 'return-header'])
def test_injected_failures_roll_back_every_write(db, monkeypatch, stage):
    proposal, _ = accept(db)
    service = AllocationService(RollbackEnvelope(db))
    record = service.allocate_proposal(3, proposal.proposal_id, 2) if stage == 'return-header' else None
    before = snapshot(db, 'v0.3-2')
    original = service.repository.query
    inserts = 0

    def fail(sql, params=(), **kwargs):
        nonlocal inserts
        if sql.startswith('INSERT INTO allocation_gpus'):
            inserts += 1
            if stage == 'second-gpu' and inserts == 2:
                raise RuntimeError('injected private failure')
        if stage == 'basis' and sql.startswith('INSERT INTO allocation_bases'):
            raise RuntimeError('injected private failure')
        if stage == 'return-header' and sql.startswith('UPDATE allocations'):
            raise RuntimeError('injected private failure')
        return original(sql, params, **kwargs)

    monkeypatch.setattr(service.repository, 'query', fail)
    with pytest.raises(RuntimeError):
        if record:
            service.finish(record.allocation_id)
        else:
            service.allocate_proposal(3, proposal.proposal_id, 2)
    assert snapshot(db, 'v0.3-2') == before


def test_different_source_conflicts_and_does_not_change_history(db):
    proposal, _ = accept(db)
    service = AllocationService(RollbackEnvelope(db))
    record = service.allocate_proposal(3, proposal.proposal_id, 2)
    before = snapshot(db, 'v0.3-2')
    with pytest.raises(AllocationConflict):
        service.allocate(3)
    assert snapshot(db, 'v0.3-2') == before
    assert service.detail(record.allocation_id).basis == record.basis


@pytest.mark.parametrize('tamper', ['gpu_count', 'response_id', 'customer_id'])
def test_changed_evidence_is_never_replayed_or_finished(db, tamper, monkeypatch):
    proposal, _ = accept(db)
    service = AllocationService(RollbackEnvelope(db))
    record = service.allocate_proposal(3, proposal.proposal_id, 2)
    # Service must reject even a repository returning corrupt evidence.
    bad = replace(record, basis=replace(record.basis, **{tamper: getattr(record.basis, tamper) + 1}))
    monkeypatch.setattr(service.repository, 'get_allocation', lambda *a, **k: bad)
    before = snapshot(db, 'v0.3-2')
    with pytest.raises((AllocationIntegrityError, AllocationConflict)):
        service.finish(record.allocation_id)
    assert snapshot(db, 'v0.3-2') == before


@pytest.mark.parametrize('column,value', [('basis_type', 'UNKNOWN'), ('gpu_count', 0),
                                          ('same_server_required', 2), ('response_id', None)])
def test_mysql_basis_checks_reject_invalid_rows(db, column, value):
    proposal, _ = accept(db)
    record = AllocationService(RollbackEnvelope(db)).allocate_proposal(3, proposal.proposal_id, 2)
    execute(db, 'SAVEPOINT constraint_check')
    with pytest.raises(mysql.connector.Error):
        execute(db, f'UPDATE allocation_bases SET {column}=%s WHERE allocation_id=%s', (value, record.allocation_id))
    execute(db, 'ROLLBACK TO SAVEPOINT constraint_check')


def test_response_fk_and_proposal_customer_fk_are_enforced(db):
    proposal, response = accept(db)
    record = AllocationService(RollbackEnvelope(db)).allocate_proposal(3, proposal.proposal_id, 2)
    for sql, params in [
        ('DELETE FROM proposal_responses WHERE response_id=%s', (response.response_id,)),
        ('UPDATE allocation_bases SET customer_id=1 WHERE allocation_id=%s', (record.allocation_id,)),
    ]:
        with pytest.raises(mysql.connector.Error):
            execute(db, sql, params)


def test_legacy_original_allocation_is_replayed_without_backfill(db):
    service = AllocationService(RollbackEnvelope(db))
    selected = choose_gpus(get_availability(service.repository, 1))
    identifier = service.repository.create(1, selected)
    before = snapshot(db, 'v0.3-2')
    replay = service.allocate(1)
    assert replay.allocation_id == identifier and replay.replayed and replay.basis is None
    assert snapshot(db, 'v0.3-2') == before
    assert service.detail(identifier).basis.basis_type == 'ORIGINAL'
    final = service.finish(identifier)
    assert final.status == 'COMPLETED' and final.basis is None
    assert execute(db, 'SELECT allocation_id FROM allocation_bases WHERE allocation_id=%s', (identifier,)) == []


def test_accept_allocate_then_same_accept_replays_and_new_alternatives_are_refused(db):
    # F1: accept -> allocate -> same accept again returns the stored response.
    proposal, response = accept(db)
    AllocationService(RollbackEnvelope(db)).allocate_proposal(3, proposal.proposal_id, 2)
    proposals = ProposalService(RollbackEnvelope(db))
    before = snapshot(db, 'v0.3-2')
    replay = proposals.respond(3, proposal.proposal_id, 2, 'ACCEPTED')
    assert replay.replayed and replay.response_id == response.response_id
    assert replay.responded_at == response.responded_at
    with pytest.raises(ProposalConflict, match='배정 이력'):
        proposals.generate(3)
    assert snapshot(db, 'v0.3-2') == before
    other = next(p for p in proposals.list_for_job(3) if p.proposal.proposal_id != proposal.proposal_id)
    assert proposals.respond(3, other.proposal.proposal_id, 2, 'REJECTED').decision == 'REJECTED'
