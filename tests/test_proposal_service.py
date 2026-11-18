"""Alternative boundaries, immutable intent and service transaction policy."""

from dataclasses import asdict, replace
from datetime import datetime
from itertools import product
from unittest.mock import MagicMock

import pytest

from app.allocation import TransactionInProgress
from app.proposal import (
    CustomerMismatch, CustomerResponse, ProposalConflict, ProposalIntegrityError, ProposalRecord,
)
from app.services.allocations import evaluate_availability
from app.services.proposals import ProposalService, generate_drafts, validate_proposal
from test_hardware_matching import gpu, requirement


def drafts(count, same, inventory, occupied=()):
    return generate_drafts(evaluate_availability(requirement(count, same), inventory, occupied))


def test_job003_distribution_and_reduction_are_distinct_and_explain_risks():
    original = requirement(4)
    inventory = [gpu(1), gpu(2), gpu(3), gpu(4, hardware="FAULTY"), gpu(5, server=2), gpu(6, server=2)]
    result = drafts(4, True, inventory)
    assert [(p.proposal_type, p.gpu_count, p.same_server_required) for p in result] == [
        ("DISTRIBUTED", 4, False), ("REDUCED_QUANTITY", 3, True)]
    assert [(c.field, c.original, c.proposed) for c in result[0].changes] == [("same_server_required", True, False)]
    assert [(c.field, c.original, c.proposed) for c in result[1].changes] == [("gpu_count", 4, 3)]
    assert "분산 학습" in result[0].cautions and "확인되지" in result[0].cautions
    assert "품질" in result[1].cautions and "보장하지" in result[1].cautions
    assert original == requirement(4)


def test_job005_reduces_quantity_without_relaxing_placement_or_changing_model():
    inventory = [gpu(1), gpu(2), gpu(3, hardware="MAINTENANCE"), gpu(4, server=2, operational="MAINTENANCE")]
    result = drafts(3, False, inventory)
    assert len(result) == 1 and result[0].gpu_count == 2 and not result[0].same_server_required
    assert result[0].gpu_model_id == 1


@pytest.mark.parametrize("count,same", list(product(range(1, 8), [False, True])))
def test_all_suggestions_obey_original_boundaries_and_are_currently_feasible(count, same):
    inventory = [gpu(1), gpu(2), gpu(3, server=2), gpu(4, hardware="FAULTY"), gpu(5, model=2)]
    availability = evaluate_availability(requirement(count, same), inventory, [2])
    result = generate_drafts(availability)
    assert len({(p.gpu_model_id, p.gpu_count, p.same_server_required) for p in result}) == len(result)
    if availability.status == "AVAILABLE":
        assert result == ()
    for draft in result:
        assert draft.gpu_model_id == availability.requirement.gpu_model_id and 0 < draft.gpu_count <= count
        proposed = replace(availability.requirement, gpu_count=draft.gpu_count, same_server_required=draft.same_server_required)
        assert proposed != availability.requirement
        assert evaluate_availability(proposed, inventory, [2]).status == "AVAILABLE"


@pytest.mark.parametrize("hardware,operational", list(product(
    ["HEALTHY", "FAULTY", "MAINTENANCE"], ["ONLINE", "MAINTENANCE", "OFFLINE"])))
def test_unavailable_equipment_never_produces_a_positive_quantity(hardware, operational):
    result = drafts(4, True, [gpu(1, hardware=hardware, operational=operational)])
    assert [p.gpu_count for p in result] == ([1] if (hardware, operational) == ("HEALTHY", "ONLINE") else [])


@pytest.mark.parametrize("inventory,occupied", [([], []), ([gpu(1, model=2)], []), ([gpu(1)], [1])])
def test_no_zero_quantity_or_cross_model_suggestions(inventory, occupied):
    assert drafts(4, True, inventory, occupied) == ()


@pytest.mark.parametrize("change", [dict(gpu_count=0), dict(gpu_count=5), dict(gpu_model_id=2),
                                  dict(same_server_required=True), dict(changes=()), dict(proposal_type="OTHER")])
def test_corrupt_proposals_are_never_used_as_consent_basis(change):
    draft = drafts(4, True, [gpu(1), gpu(2), gpu(3), gpu(4, server=2)])[0]
    with pytest.raises(ProposalIntegrityError):
        validate_proposal(replace(draft, **change), requirement(4))


@pytest.fixture
def service():
    cn = MagicMock(in_transaction=False)
    service = ProposalService(cn)
    repo = service.repository = MagicMock()
    repo.get_requirement.return_value = requirement(4)
    repo.owner.return_value = 1
    draft = drafts(4, True, [gpu(1), gpu(2), gpu(3), gpu(4, server=2)])[0]
    record = ProposalRecord(7, 1, 1, **asdict(draft), created_at=datetime(2026, 11, 8))
    # asdict recursively transforms changes; preserve the immutable value objects.
    record = replace(record, changes=draft.changes)
    repo.get_proposal.return_value = record
    repo.response_for.return_value = None
    repo.accepted_for_job.return_value = None
    # Default: the Job has no allocation history (MagicMock would otherwise be truthy).
    repo.has_allocation_history.return_value = False
    repo.create_response.return_value = CustomerResponse(9, 7, 1, 1, "ACCEPTED", datetime(2026, 11, 8))
    return service


def call_order(repo, names):
    return [call[0] for call in repo.mock_calls if call[0] in names]


def test_acceptance_is_intent_without_any_allocation_or_resource_recheck(service):
    assert service.respond(1, 7, 1, "ACCEPTED").decision == "ACCEPTED"
    service.repository.create_response.assert_called_once()
    service.repository.lock_inventory.assert_not_called()
    service.repository.create.assert_not_called()
    service.connection.commit.assert_called_once()


def test_wrong_customer_is_rejected_before_reading_or_writing_proposal(service):
    with pytest.raises(CustomerMismatch):
        service.respond(1, 7, 2, "ACCEPTED")
    service.repository.get_proposal.assert_not_called()
    service.repository.create_response.assert_not_called()
    service.connection.rollback.assert_called_once()


def test_wrong_job_rejected_without_writing(service):
    service.repository.get_proposal.return_value = replace(service.repository.get_proposal.return_value, job_request_id=2)
    with pytest.raises(ProposalConflict):
        service.respond(1, 7, 1, "ACCEPTED")
    service.repository.create_response.assert_not_called()


@pytest.mark.parametrize("decision", ["ACCEPTED", "REJECTED"])
def test_same_response_replays_id_and_timestamp_and_opposite_response_conflicts(service, decision):
    original = replace(service.repository.create_response.return_value, decision=decision)
    service.repository.response_for.return_value = original
    replay = service.respond(1, 7, 1, decision)
    assert replay.replayed and replace(replay, replayed=False) == original
    with pytest.raises(ProposalConflict):
        service.respond(1, 7, 1, "REJECTED" if decision == "ACCEPTED" else "ACCEPTED")
    service.repository.create_response.assert_not_called()


def test_different_accepted_proposal_conflicts(service):
    service.repository.accepted_for_job.return_value = CustomerResponse(10, 8, 1, 1, "ACCEPTED", datetime(2026, 11, 8))
    with pytest.raises(ProposalConflict):
        service.respond(1, 7, 1, "ACCEPTED")
    service.repository.create_response.assert_not_called()


def test_failed_response_write_rolls_back_and_does_not_commit(service):
    service.repository.create_response.side_effect = RuntimeError("simulated write failure")
    with pytest.raises(RuntimeError):
        service.respond(1, 7, 1, "ACCEPTED")
    service.connection.rollback.assert_called_once()
    service.connection.commit.assert_not_called()


def test_ambient_transaction_is_never_taken_over(service):
    service.connection.in_transaction = True
    with pytest.raises(TransactionInProgress):
        service.generate(1)
    service.connection.rollback.assert_not_called()
    service.connection.commit.assert_not_called()


def test_invalid_decision_is_rejected_before_any_transaction(service):
    with pytest.raises(ProposalConflict):
        service.respond(1, 7, 1, "MAYBE")
    service.connection.start_transaction.assert_not_called()


# F1: a Job with any allocation history (ACTIVE, COMPLETED or FAILED) has used
# its single execution. New alternatives or new acceptances would be consent
# records that can never be executed, so they are refused without any write.

def test_generate_refuses_job_with_allocation_history_after_model_and_job_locks(service):
    service.repository.has_allocation_history.return_value = True
    with pytest.raises(ProposalConflict, match="배정 이력"):
        service.generate(1)
    service.repository.has_allocation_history.assert_called_once_with(1)
    assert call_order(service.repository, ("lock_request", "lock_inventory", "has_allocation_history")) == [
        "lock_request", "lock_inventory", "has_allocation_history"]
    service.repository.find_conditions.assert_not_called()
    service.repository.create_proposal.assert_not_called()
    service.connection.rollback.assert_called_once()
    service.connection.commit.assert_not_called()


def test_generate_without_allocation_history_still_creates_proposals(service):
    repo = service.repository
    repo.lock_inventory.return_value = (gpu(1), gpu(2), gpu(3), gpu(4, server=2))
    repo.occupied_gpu_ids.return_value = ()
    repo.find_conditions.return_value = None
    repo.create_proposal.return_value = 7
    result = service.generate(1)
    assert result.status == "PROPOSALS_AVAILABLE" and result.proposals
    repo.has_allocation_history.assert_called_once_with(1)
    assert repo.create_proposal.call_count == 2
    service.connection.commit.assert_called_once()


def test_new_acceptance_refused_for_job_with_allocation_history(service):
    service.repository.has_allocation_history.return_value = True
    with pytest.raises(ProposalConflict, match="배정 이력"):
        service.respond(1, 7, 1, "ACCEPTED")
    assert call_order(service.repository, ("lock_request", "get_proposal", "response_for", "accepted_for_job",
                                           "has_allocation_history")) == [
        "lock_request", "get_proposal", "response_for", "accepted_for_job", "has_allocation_history"]
    service.repository.create_response.assert_not_called()
    service.connection.rollback.assert_called_once()
    service.connection.commit.assert_not_called()


def test_new_rejection_is_still_recorded_for_job_with_allocation_history(service):
    service.repository.has_allocation_history.return_value = True
    service.repository.create_response.return_value = CustomerResponse(9, 7, 1, 1, "REJECTED", datetime(2026, 11, 8))
    assert service.respond(1, 7, 1, "REJECTED").decision == "REJECTED"
    service.repository.create_response.assert_called_once()
    service.connection.commit.assert_called_once()


@pytest.mark.parametrize("decision", ["ACCEPTED", "REJECTED"])
def test_existing_response_still_replays_after_allocation(service, decision):
    # accept -> allocate -> same accept again must return the stored response.
    service.repository.has_allocation_history.return_value = True
    original = replace(service.repository.create_response.return_value, decision=decision)
    service.repository.response_for.return_value = original
    replay = service.respond(1, 7, 1, decision)
    assert replay.replayed and replace(replay, replayed=False) == original
    service.repository.create_response.assert_not_called()
    service.connection.commit.assert_called_once()
