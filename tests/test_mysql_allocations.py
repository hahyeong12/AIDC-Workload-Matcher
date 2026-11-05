"""Real SQL constraints and service writes inside rolled-back private fixtures."""

from itertools import product

import pytest

from allocation_helpers import RollbackEnvelope, allocation_case, execute
from app.allocation import AllocationConflict, AllocationNotFound, InsufficientResources, TransactionInProgress
from app.db import DatabaseOperationError
from app.repositories.allocations import AllocationRepository
from app.repositories.hardware import HardwareRepository
from app.services.allocations import AllocationService, get_availability
from app.services.hardware import check_hardware
from mysql_audit import snapshot


pytestmark = pytest.mark.mysql


def test_available_count_and_no_partial_allocation_on_insufficient_quantity(allocation_case):
    case = allocation_case
    first = case["service"].allocate(case["new_job"](2))
    assert tuple(item.gpu_id for item in first.gpus) == case["gpu_ids"][:2]
    three = case["new_job"](3)
    availability = get_availability(AllocationRepository(case["db"]), three)
    assert availability.available_gpu_count == 2
    assert check_hardware(HardwareRepository(case["db"]), three).status == "HARDWARE_FEASIBLE"
    before = snapshot(case["db"], "v0.2-2")
    with pytest.raises(InsufficientResources):
        case["service"].allocate(three)
    assert snapshot(case["db"], "v0.2-2") == before
    assert execute(case["db"], "SELECT status FROM allocations WHERE job_request_id = %s", (three,)) == []


def test_completion_returns_gpus_and_reuse_preserves_same_gpu_history(allocation_case):
    case = allocation_case
    repo = AllocationRepository(case["db"])
    original_job = case["new_job"](2)
    first = case["service"].allocate(original_job)
    completed = case["service"].finish(first.allocation_id)
    assert completed.status == "COMPLETED" and completed.ended_at is not None
    assert all(item.released_at == completed.ended_at for item in completed.gpus)
    next_job = case["new_job"](2)
    assert get_availability(repo, next_job).available_gpu_count == 4
    second = case["service"].allocate(next_job)
    assert [item.gpu_id for item in first.gpus] == [item.gpu_id for item in second.gpus]
    history = repo.history_for_gpu(first.gpus[0].gpu_id)
    assert [(row[0], row[2]) for row in history] == [(first.allocation_id, "COMPLETED"), (second.allocation_id, "ACTIVE")]
    assert history[0][5] is not None and history[1][5] is None


def test_repeated_allocation_and_completion_are_idempotent_including_after_completion(allocation_case):
    case = allocation_case
    job = case["new_job"](2)
    first = case["service"].allocate(job)
    again = case["service"].allocate(job)
    assert again.replayed and again.allocation_id == first.allocation_id
    completed = case["service"].finish(first.allocation_id)
    repeated = case["service"].finish(first.allocation_id)
    assert repeated.replayed and repeated.ended_at == completed.ended_at
    replay = case["service"].allocate(job)
    assert replay.replayed and replay.status == "COMPLETED"
    assert replay.allocation_id == first.allocation_id
    assert execute(case["db"], "SELECT COUNT(*) FROM allocations WHERE job_request_id = %s", (job,)) == [(1,)]


def test_failed_means_failure_after_committed_assignment_and_returns_all_gpus(allocation_case):
    case = allocation_case
    record = case["service"].allocate(case["new_job"](2))
    failed = case["service"].finish(record.allocation_id, "FAILED")
    assert failed.status == "FAILED" and all(gpu.released_at == failed.ended_at for gpu in failed.gpus)
    assert case["service"].finish(record.allocation_id, "FAILED").replayed
    assert get_availability(AllocationRepository(case["db"]), case["new_job"](4)).available_gpu_count == 4
    with pytest.raises(AllocationConflict):
        case["service"].finish(record.allocation_id, "COMPLETED")


def test_db_unique_blocks_two_active_allocations_for_one_job(allocation_case):
    import mysql.connector
    case = allocation_case
    job = case["new_job"](2)
    case["service"].allocate(job)
    with pytest.raises(mysql.connector.Error) as caught:
        execute(case["db"], "INSERT INTO allocations (job_request_id) VALUES (%s)", (job,))
    assert caught.value.errno == 1062
    assert execute(case["db"], "SELECT active_job_request_id FROM allocations WHERE job_request_id = %s", (job,)) == [(job,)]


def test_db_unique_blocks_same_gpu_in_two_unreleased_allocations(allocation_case):
    import mysql.connector
    case = allocation_case
    first = case["service"].allocate(case["new_job"](2))
    other = execute(case["db"], "INSERT INTO allocations (job_request_id) VALUES (%s)", (case["new_job"](2),))
    with pytest.raises(mysql.connector.Error) as caught:
        execute(case["db"], "INSERT INTO allocation_gpus (allocation_id, gpu_id, server_id) VALUES (%s, %s, %s)",
                (other, first.gpus[0].gpu_id, case["server"]))
    assert caught.value.errno == 1062
    assert execute(case["db"], "SELECT active_gpu_id FROM allocation_gpus WHERE gpu_id = %s", (first.gpus[0].gpu_id,)) == [
        (first.gpus[0].gpu_id,)]


@pytest.mark.parametrize("same,count,expected", [(True, 3, False), (True, 2, True), (False, 3, True), (False, 4, True)])
def test_same_server_and_distributed_exact_combinations(allocation_case, same, count, expected):
    case = allocation_case
    second = execute(case["db"], "INSERT INTO servers (server_name) VALUES (%s)", (case["sample"]["token"] + "second",))
    for gpu_id in case["gpu_ids"][2:]:
        execute(case["db"], "UPDATE gpus SET server_id = %s WHERE gpu_id = %s", (second, gpu_id))
    job = case["new_job"](count, same)
    before = snapshot(case["db"], "v0.2-2")
    if not expected:
        with pytest.raises(InsufficientResources):
            case["service"].allocate(job)
        assert snapshot(case["db"], "v0.2-2") == before
    else:
        record = case["service"].allocate(job)
        assert tuple(item.gpu_id for item in record.gpus) == case["gpu_ids"][:count]
        assert len(record.gpus) == count
        assert {item.server_id for item in record.gpus} == ({case["server"]} if same else {case["server"], second})


@pytest.mark.parametrize("hardware,operational", list(product(
    ["HEALTHY", "FAULTY", "MAINTENANCE"], ["ONLINE", "MAINTENANCE", "OFFLINE"])))
def test_rechecks_actual_equipment_status_before_allocation(db, sample, hardware, operational):
    execute(db, "UPDATE gpus SET hardware_status = %s WHERE gpu_id = %s", (hardware, sample["gpu"]))
    execute(db, "UPDATE servers SET operational_status = %s WHERE server_id = %s", (operational, sample["server"]))
    execute(db, "INSERT INTO job_requirements (job_request_id, gpu_model_id, gpu_count, same_server_required) "
            "VALUES (%s, %s, 1, 1)", (sample["job"], sample["model"]))
    service = AllocationService(RollbackEnvelope(db))
    if hardware == "HEALTHY" and operational == "ONLINE":
        assert service.allocate(sample["job"]).gpus[0].gpu_id == sample["gpu"]
    else:
        before = snapshot(db, "v0.2-2")
        with pytest.raises(InsufficientResources):
            service.allocate(sample["job"])
        assert snapshot(db, "v0.2-2") == before


def test_error_after_first_gpu_insert_rolls_back_header_and_all_gpu_rows(allocation_case, monkeypatch):
    case = allocation_case
    job = case["new_job"](2)
    before = snapshot(case["db"], "v0.2-2")
    query = case["service"].repository.query
    inserts = 0
    def inject(sql, params=(), **options):
        nonlocal inserts
        if sql.startswith("INSERT INTO allocation_gpus"):
            inserts += 1
            if inserts == 2:
                # Actual MySQL FK rejection after the first successful insert.
                params = (params[0], 18446744073709551615, params[2], params[3])
        return query(sql, params, **options)
    monkeypatch.setattr(case["service"].repository, "query", inject)
    with pytest.raises(DatabaseOperationError) as caught:
        case["service"].allocate(job)
    assert caught.value.errno in (1216, 1452)
    assert inserts == 2
    assert snapshot(case["db"], "v0.2-2") == before


def test_completion_error_after_release_rolls_back_all_changes(allocation_case, monkeypatch):
    case = allocation_case
    record = case["service"].allocate(case["new_job"](2))
    before = snapshot(case["db"], "v0.2-2")
    query = case["service"].repository.query
    def inject(sql, params=(), **options):
        if sql.startswith("UPDATE allocations SET status"):
            params = ("INVALID", params[1], params[2])  # Real CHECK failure, after GPU release UPDATE.
        return query(sql, params, **options)
    monkeypatch.setattr(case["service"].repository, "query", inject)
    with pytest.raises(DatabaseOperationError) as caught:
        case["service"].finish(record.allocation_id)
    assert caught.value.errno == 3819
    assert snapshot(case["db"], "v0.2-2") == before


@pytest.mark.parametrize("status,ended", [("UNKNOWN", None), ("COMPLETED", None), ("FAILED", None),
                                         ("ACTIVE", "2026-11-05 00:00:00")])
def test_db_rejects_invalid_status_and_timestamp_combinations(allocation_case, status, ended):
    import mysql.connector
    case = allocation_case
    with pytest.raises(mysql.connector.Error) as caught:
        execute(case["db"], "INSERT INTO allocations (job_request_id, status, ended_at) VALUES (%s, %s, %s)",
                (case["new_job"](1), status, ended))
    assert caught.value.errno == 3819


def test_missing_allocation_and_ambient_transaction_are_safe(allocation_case):
    case = allocation_case
    before = snapshot(case["db"], "v0.2-2")
    with pytest.raises(AllocationNotFound):
        case["service"].finish(18446744073709551615)
    with pytest.raises(TransactionInProgress):
        AllocationService(case["db"]).allocate(case["new_job"](1))
    # The caller's original fixture and existing rows still exist.
    assert execute(case["db"], "SELECT gpu_id FROM gpus WHERE gpu_id = %s", (case["gpu_ids"][0],))
    assert snapshot(case["db"], "v0.2-2")["allocations"] == before["allocations"]
