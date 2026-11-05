"""Pure availability/selection and transaction ownership/error behavior."""

from dataclasses import replace
from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest

from app.allocation import (
    AllocationGPU, AllocationIntegrityError, AllocationResult, InsufficientResources, TransactionInProgress,
)
from app.db import DatabaseOperationError
from app.matching import InvalidJobID
from app.services.allocations import (
    AllocationService, allocation_transaction, choose_gpus, evaluate_availability, validate_allocation,
)
from test_hardware_matching import gpu, requirement


def test_four_healthy_two_allocated_leaves_two_and_three_cannot_allocate():
    inventory = [gpu(i) for i in range(1, 5)]
    available = evaluate_availability(requirement(3, False), inventory, [1, 2])
    assert available.available_gpu_count == 2
    assert available.status == "INSUFFICIENT_RESOURCES"
    assert available.reason_codes == ("AVAILABLE_QUANTITY_SHORTAGE",)
    assert [item.gpu_id for item in available.available_gpus] == [3, 4]
    with pytest.raises(InsufficientResources):
        choose_gpus(available)


@pytest.mark.parametrize("same,count,expected", [(True, 2, [1, 2]), (False, 3, [1, 2, 3]), (False, 4, [1, 2, 3, 4])])
def test_selection_is_deterministic_and_records_exact_requested_count(same, count, expected):
    inventory = [gpu(4, server=2), gpu(2), gpu(3, server=2), gpu(1)]
    selected = choose_gpus(evaluate_availability(requirement(count, same), inventory, []))
    assert [item.gpu_id for item in selected] == expected


def test_no_same_server_selection_when_only_aggregate_is_enough():
    availability = evaluate_availability(requirement(3, True), [gpu(1), gpu(2), gpu(3, server=2)], [])
    assert availability.available_gpu_count == 3
    assert availability.reason_codes == ("AVAILABLE_SAME_SERVER_SHORTAGE",)
    with pytest.raises(InsufficientResources):
        choose_gpus(availability)


def test_all_held_is_quantity_shortage_not_missing_model():
    result = evaluate_availability(requirement(1, False), [gpu(1)], [1])
    assert result.reason_codes == ("AVAILABLE_QUANTITY_SHORTAGE",)


def test_other_models_and_bad_equipment_still_excluded():
    result = evaluate_availability(requirement(2, False), [
        gpu(1), gpu(2, hardware="FAULTY"), gpu(3, operational="OFFLINE"), gpu(4, model=2)], [])
    assert result.available_gpu_count == 1
    assert [item.gpu_id for item in result.available_gpus] == [1]


def test_transaction_commits_only_after_success():
    connection = MagicMock(in_transaction=False)
    with allocation_transaction(connection):
        connection.commit.assert_not_called()
    connection.start_transaction.assert_called_once_with(isolation_level="READ COMMITTED")
    connection.commit.assert_called_once()
    connection.rollback.assert_not_called()


@pytest.mark.parametrize("stage", ["body", "begin", "commit"])
def test_transaction_failure_rolls_back(stage):
    connection = MagicMock(in_transaction=False)
    error = RuntimeError("private-error")
    if stage == "begin":
        connection.start_transaction.side_effect = error
    if stage == "commit":
        connection.commit.side_effect = error
    with pytest.raises((RuntimeError, DatabaseOperationError)):
        with allocation_transaction(connection):
            if stage == "body":
                raise error
    connection.rollback.assert_called_once()
    if stage != "commit":
        connection.commit.assert_not_called()


def test_existing_transaction_is_not_committed_or_rolled_back():
    connection = MagicMock(in_transaction=True)
    with pytest.raises(TransactionInProgress):
        AllocationService(connection).allocate(1)
    connection.start_transaction.assert_not_called()
    connection.commit.assert_not_called()
    connection.rollback.assert_not_called()


def test_bad_id_is_rejected_before_starting_transaction():
    connection = MagicMock(in_transaction=False)
    with pytest.raises(InvalidJobID):
        AllocationService(connection).allocate("1 OR 1=1")
    connection.start_transaction.assert_not_called()


@pytest.mark.parametrize("case", ["partial", "different-servers", "active-released", "terminal-unreleased", "wrong-release-time"])
def test_inconsistent_records_are_never_replayed_or_finished(case):
    now = datetime(2026, 11, 5)
    record = AllocationResult(1, 1, "ACTIVE", now, None,
                              (AllocationGPU(1, 1, now, None), AllocationGPU(2, 1, now, None)))
    if case == "partial":
        record = replace(record, gpus=record.gpus[:1])
    elif case == "different-servers":
        record = replace(record, gpus=(record.gpus[0], replace(record.gpus[1], server_id=2)))
    elif case == "active-released":
        record = replace(record, gpus=tuple(replace(item, released_at=now) for item in record.gpus))
    elif case == "terminal-unreleased":
        record = replace(record, status="COMPLETED", ended_at=now)
    else:
        record = replace(record, status="COMPLETED", ended_at=now + timedelta(seconds=1),
                         gpus=tuple(replace(item, released_at=now) for item in record.gpus))
    with pytest.raises(AllocationIntegrityError):
        validate_allocation(record, requirement(2, True))
