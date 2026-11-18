"""Real MySQL proposals/consent with private rows rolled back after every test."""

from itertools import product

import mysql.connector
import pytest

from allocation_helpers import RollbackEnvelope, execute
from app.db import DatabaseOperationError
from app.proposal import CustomerMismatch, ProposalConflict, ProposalNotFound
from app.services.allocations import AllocationService, get_availability
from app.services.proposals import ProposalService
from database.inspect_schema import inspect
from mysql_audit import snapshot
from proposal_helpers import proposal_case


pytestmark = pytest.mark.mysql


@pytest.mark.parametrize("job,expected", [(3, [("DISTRIBUTED", 4, False), ("REDUCED_QUANTITY", 3, True)]),
                                          (5, [("REDUCED_QUANTITY", 2, False)])])
def test_actual_seed_alternatives_preserve_originals_and_gpu_occupancy(db, job, expected):
    before = snapshot(db, "v0.2-2")
    service = ProposalService(RollbackEnvelope(db))
    result = service.generate(job)
    assert result.status == "PROPOSALS_AVAILABLE"
    assert [(p.proposal_type, p.gpu_count, p.same_server_required) for p in result.proposals] == expected
    assert snapshot(db, "v0.2-2") == before
    again = service.generate(job)
    assert all(p.replayed for p in again.proposals)
    assert [(p.proposal_id, p.created_at) for p in result.proposals] == [(p.proposal_id, p.created_at) for p in again.proposals]
    assert len(service.list_for_job(job)) == len(expected)


@pytest.mark.parametrize("job", [1, 2, 4])
def test_feasible_original_does_not_create_unnecessary_proposals(db, job):
    before = snapshot(db, "v0.3-1")
    result = ProposalService(RollbackEnvelope(db)).generate(job)
    assert result.status == "ORIGINAL_AVAILABLE" and result.proposals == ()
    assert snapshot(db, "v0.3-1") == before


@pytest.mark.parametrize("hardware,operational", list(product(
    ["HEALTHY", "FAULTY", "MAINTENANCE"], ["ONLINE", "MAINTENANCE", "OFFLINE"])))
def test_only_online_healthy_unheld_equipment_can_support_a_proposal(db, sample, hardware, operational):
    execute(db, "UPDATE gpus SET hardware_status = %s WHERE gpu_id = %s", (hardware, sample["gpu"]))
    execute(db, "UPDATE servers SET operational_status = %s WHERE server_id = %s", (operational, sample["server"]))
    execute(db, "INSERT INTO job_requirements VALUES (%s, %s, 2, 1)", (sample["job"], sample["model"]))
    before = snapshot(db, "v0.2-2")
    result = ProposalService(RollbackEnvelope(db)).generate(sample["job"])
    healthy = hardware == "HEALTHY" and operational == "ONLINE"
    assert [p.gpu_count for p in result.proposals] == ([1] if healthy else [])
    if not healthy:
        assert result.status == "NO_FEASIBLE_ALTERNATIVES" and "NO_FREE_GPUS" in result.reason_codes
    assert snapshot(db, "v0.2-2") == before


def test_model_without_devices_does_not_suggest_other_model(db, sample):
    model = execute(db, "INSERT INTO gpu_models (manufacturer, model_name, memory_gb) VALUES ('TEST', %s, 80)",
                    (sample["token"] + "empty",))
    execute(db, "INSERT INTO job_requirements VALUES (%s, %s, 3, 0)", (sample["job"], model))
    result = ProposalService(RollbackEnvelope(db)).generate(sample["job"])
    assert result.proposals == () and "MODEL_UNAVAILABLE" in result.reason_codes


@pytest.mark.parametrize("count,expected", [(3, [2]), (5, [])])
def test_active_allocations_are_excluded_without_being_released(proposal_case, count, expected):
    case = proposal_case
    AllocationService(RollbackEnvelope(case["db"])).allocate(case["new_job"](count))
    before = snapshot(case["db"], "v0.2-2")
    result = case["service"].generate(case["job_id"])
    assert [p.gpu_count for p in result.proposals] == expected
    if expected:
        assert result.proposals[0].same_server_required
    assert snapshot(case["db"], "v0.2-2") == before


@pytest.mark.parametrize("decision", ["ACCEPTED", "REJECTED"])
def test_terminal_response_is_immutable_idempotent_and_queryable(proposal_case, decision):
    case = proposal_case
    service, job, customer = case["service"], case["job_id"], case["customer"]
    proposals = service.generate(job).proposals
    before = snapshot(case["db"], "v0.2-2")
    original = service.respond(job, proposals[0].proposal_id, customer, decision)
    again = service.respond(job, proposals[0].proposal_id, customer, decision)
    assert again.replayed and again.response_id == original.response_id and again.responded_at == original.responded_at
    with pytest.raises(ProposalConflict):
        service.respond(job, proposals[0].proposal_id, customer, "REJECTED" if decision == "ACCEPTED" else "ACCEPTED")
    assert service.detail(proposals[0].proposal_id).response == original
    assert service.history(job) == (original,)
    assert [p.proposal_id for p in service.generate(job).proposals] == [p.proposal_id for p in proposals]
    if decision == "ACCEPTED":
        evidence = service.accepted(job)
        assert evidence.basis_kind == "ACCEPTED_PROPOSAL" and evidence.response == original
    else:
        assert service.accepted(job) is None
    assert snapshot(case["db"], "v0.2-2") == before


def test_reject_then_accept_different_proposal_preserves_both_intents(proposal_case):
    case = proposal_case
    service, job = case["service"], case["job_id"]
    first, second = service.generate(job).proposals
    rejected = service.respond(job, first.proposal_id, case["customer"], "REJECTED")
    accepted = service.respond(job, second.proposal_id, case["customer"], "ACCEPTED")
    assert service.history(job) == (rejected, accepted)
    assert service.detail(first.proposal_id).response.decision == "REJECTED"


def test_changed_resources_allow_new_conditions_without_overwriting_rejected_history(proposal_case):
    case = proposal_case
    service, job = case["service"], case["job_id"]
    first, reduced_three = service.generate(job).proposals
    rejected = service.respond(job, reduced_three.proposal_id, case["customer"], "REJECTED")
    AllocationService(RollbackEnvelope(case["db"])).allocate(case["new_job"](1))
    current = service.generate(job).proposals
    assert [(p.gpu_count, p.same_server_required) for p in current] == [(4, False), (2, True)]
    assert current[0].proposal_id == first.proposal_id and current[0].created_at == first.created_at
    assert len(service.list_for_job(job)) == 3
    assert service.detail(reduced_three.proposal_id).response == rejected


def test_two_different_acceptances_for_one_job_are_rejected(proposal_case):
    case = proposal_case
    first, second = case["service"].generate(case["job_id"]).proposals
    case["service"].respond(case["job_id"], first.proposal_id, case["customer"], "ACCEPTED")
    before = snapshot(case["db"], "v0.3-1")
    with pytest.raises(ProposalConflict):
        case["service"].respond(case["job_id"], second.proposal_id, case["customer"], "ACCEPTED")
    assert snapshot(case["db"], "v0.3-1") == before
    assert case["service"].respond(case["job_id"], second.proposal_id, case["customer"], "REJECTED").decision == "REJECTED"


@pytest.mark.parametrize("case_name", ["wrong-customer", "wrong-job", "missing-proposal"])
def test_response_relationships_and_missing_proposals_fail_without_mutation(proposal_case, case_name):
    case = proposal_case
    first = case["service"].generate(case["job_id"]).proposals[0]
    job, proposal, customer = case["job_id"], first.proposal_id, case["customer"]
    error = ProposalConflict
    if case_name == "wrong-customer": customer, error = 1, CustomerMismatch
    elif case_name == "wrong-job": job = case["new_job"](4, True)
    else: proposal, error = 18446744073709551615, ProposalNotFound
    before = snapshot(case["db"], "v0.3-1")
    with pytest.raises(error): case["service"].respond(job, proposal, customer, "ACCEPTED")
    assert snapshot(case["db"], "v0.3-1") == before


def test_stale_proposal_may_record_consent_but_does_not_guarantee_resources(proposal_case):
    case = proposal_case
    proposal = case["service"].generate(case["job_id"]).proposals[0]
    AllocationService(RollbackEnvelope(case["db"])).allocate(case["new_job"](4))
    before = snapshot(case["db"], "v0.2-2")
    response = case["service"].respond(case["job_id"], proposal.proposal_id, case["customer"], "ACCEPTED")
    assert response.decision == "ACCEPTED" and "재검증" in response.scope
    assert get_availability(case["service"].repository, case["job_id"]).available_gpu_count == 1
    assert snapshot(case["db"], "v0.2-2") == before


def test_response_error_after_real_insert_rolls_back_all_response_changes(proposal_case, monkeypatch):
    case = proposal_case
    first, second = case["service"].generate(case["job_id"]).proposals
    before = snapshot(case["db"], "v0.3-1")
    repo = case["service"].repository
    create = repo.create_response
    def fail_after_insert(*args):
        result = create(*args)
        repo.query("INSERT INTO proposal_responses (proposal_id, job_request_id, customer_id, decision) "
                   "VALUES (%s, %s, %s, 'INVALID')", (second.proposal_id, case["job_id"], case["customer"]), write=True)
        return result
    monkeypatch.setattr(repo, "create_response", fail_after_insert)
    with pytest.raises(DatabaseOperationError) as error:
        case["service"].respond(case["job_id"], first.proposal_id, case["customer"], "ACCEPTED")
    assert error.value.errno == 3819
    assert snapshot(case["db"], "v0.3-1") == before


def test_generation_error_after_first_proposal_rolls_back_all_proposals(proposal_case, monkeypatch):
    case = proposal_case
    repo = case["service"].repository
    create = repo.create_proposal
    calls = []
    def fail_second(job, customer, draft):
        calls.append(draft)
        return create(job, customer, draft) if len(calls) == 1 else create(18446744073709551615, customer, draft)
    monkeypatch.setattr(repo, "create_proposal", fail_second)
    before = snapshot(case["db"], "v0.3-1")
    with pytest.raises(DatabaseOperationError): case["service"].generate(case["job_id"])
    assert len(calls) == 2 and snapshot(case["db"], "v0.3-1") == before


def test_database_unique_conditions_and_acceptance_guards(proposal_case):
    case = proposal_case
    first, second = case["service"].generate(case["job_id"]).proposals
    with pytest.raises(mysql.connector.Error) as error:
        execute(case["db"], "INSERT INTO job_proposals (job_request_id, customer_id, gpu_model_id, gpu_count, "
                "same_server_required, proposal_type, reason, changes, cautions) "
                "SELECT job_request_id, customer_id, gpu_model_id, gpu_count, same_server_required, proposal_type, "
                "reason, changes, cautions FROM job_proposals WHERE proposal_id = %s", (first.proposal_id,))
    assert error.value.errno == 1062
    case["service"].respond(case["job_id"], first.proposal_id, case["customer"], "ACCEPTED")
    for proposal in (first, second):
        with pytest.raises(mysql.connector.Error) as error:
            execute(case["db"], "INSERT INTO proposal_responses (proposal_id, job_request_id, customer_id, decision) "
                    "VALUES (%s, %s, %s, 'ACCEPTED')", (proposal.proposal_id, case["job_id"], case["customer"]))
        assert error.value.errno == 1062


@pytest.mark.parametrize("bad_field", ["customer", "job", "proposal"])
def test_composite_fk_rejects_forged_response_identity(proposal_case, bad_field):
    case = proposal_case
    proposal = case["service"].generate(case["job_id"]).proposals[0].proposal_id
    job, customer = case["job_id"], case["customer"]
    if bad_field == "customer": customer = 1
    elif bad_field == "job": job = case["new_job"](4, True)
    else: proposal = 18446744073709551615
    with pytest.raises(mysql.connector.Error) as error:
        execute(case["db"], "INSERT INTO proposal_responses (proposal_id, job_request_id, customer_id, decision) "
                "VALUES (%s, %s, %s, 'ACCEPTED')", (proposal, job, customer))
    assert error.value.errno in (1216, 1452) and error.value.sqlstate == "23000"


@pytest.mark.parametrize("field,value", [("gpu_count", 0), ("gpu_count", -1), ("same_server_required", 2),
    ("proposal_type", "UNKNOWN"), ("reason", " "), ("cautions", ""), ("changes", "{}")])
def test_database_rejects_invalid_proposal_fields(proposal_case, field, value):
    case = proposal_case
    fields = dict(job_request_id=case["job_id"], customer_id=case["customer"], gpu_model_id=case["sample"]["model"],
                  gpu_count=2, same_server_required=1, proposal_type="REDUCED_QUANTITY", reason="reason", changes="[]", cautions="caution")
    fields[field] = value
    with pytest.raises(mysql.connector.Error) as error:
        execute(case["db"], "INSERT INTO job_proposals (" + ", ".join(fields) + ") VALUES (" +
                ", ".join(["%s"] * len(fields)) + ")", tuple(fields.values()))
    assert error.value.errno == 3819


@pytest.mark.parametrize("field", ["job_request_id", "gpu_model_id", "customer_id"])
def test_database_rejects_missing_proposal_parents(proposal_case, field):
    case = proposal_case
    fields = dict(job_request_id=case["job_id"], customer_id=case["customer"], gpu_model_id=case["sample"]["model"],
                  gpu_count=2, same_server_required=1, proposal_type="REDUCED_QUANTITY", reason="reason", changes="[]", cautions="caution")
    fields[field] = 18446744073709551615
    with pytest.raises(mysql.connector.Error) as error:
        execute(case["db"], "INSERT INTO job_proposals (" + ", ".join(fields) + ") VALUES (" +
                ", ".join(["%s"] * len(fields)) + ")", tuple(fields.values()))
    assert error.value.errno in (1216, 1452)


# F1 regression: one execution per Job. Once any allocation exists (ACTIVE,
# COMPLETED or FAILED), new alternatives and new ACCEPTED responses would be
# consent that can never be executed. They are refused without any row change.

def allocated_job(case, status):
    """A private 4-GPU distributed Job with one allocation in the given state.

    Inventory is 3 + 2 GPUs. For ended allocations a second private Job keeps
    2 GPUs busy, so the original stays short and alternatives would otherwise
    be generated (that is the behaviour the fix must refuse).
    """
    allocations = AllocationService(RollbackEnvelope(case["db"]))
    job = case["new_job"](4)
    record = allocations.allocate(job)
    if status != "ACTIVE":
        allocations.finish(record.allocation_id, status)
        allocations.allocate(case["new_job"](2))
    return job


@pytest.mark.parametrize("status", ["ACTIVE", "COMPLETED", "FAILED"])
def test_generation_is_refused_for_job_with_allocation_history(proposal_case, status):
    case = proposal_case
    job = allocated_job(case, status)
    assert get_availability(case["service"].repository, job).status == "INSUFFICIENT_RESOURCES"
    version = inspect(case["db"])["schema_version"]
    before = snapshot(case["db"], version)
    with pytest.raises(ProposalConflict, match="배정 이력"):
        case["service"].generate(job)
    assert snapshot(case["db"], version) == before
    assert case["service"].list_for_job(job) == ()


@pytest.mark.parametrize("status", ["ACTIVE", "COMPLETED", "FAILED"])
def test_stale_proposal_cannot_be_accepted_after_original_execution_but_can_be_rejected(proposal_case, status):
    case = proposal_case
    service, db = case["service"], case["db"]
    allocations = AllocationService(RollbackEnvelope(db))
    blocker = allocations.allocate(case["new_job"](2))
    job = case["new_job"](4)
    (proposal,) = service.generate(job).proposals          # reduced to the 3 free GPUs
    allocations.finish(blocker.allocation_id)
    record = allocations.allocate(job)                       # original now runs on 4 GPUs
    if status != "ACTIVE":
        allocations.finish(record.allocation_id, status)
    version = inspect(db)["schema_version"]
    before = snapshot(db, version)
    with pytest.raises(ProposalConflict, match="배정 이력"):
        service.respond(job, proposal.proposal_id, case["customer"], "ACCEPTED")
    assert snapshot(db, version) == before
    rejected = service.respond(job, proposal.proposal_id, case["customer"], "REJECTED")
    assert rejected.decision == "REJECTED" and not rejected.replayed
    assert service.respond(job, proposal.proposal_id, case["customer"], "REJECTED").replayed
    assert service.history(job) == (rejected,)
