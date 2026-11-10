"""Validation boundaries, transaction ownership and safe database errors."""

from unittest.mock import Mock

import pytest

from app.allocation import TransactionInProgress
from app.db import DatabaseOperationError
from app.job import InvalidJobInput, validate_registration
from app.services.jobs import JobService


VALID = dict(customer_id=1, title="  학습  ", original_request="  A100 8개 원문\n",
             gpu_model_id=2, gpu_count=3, same_server_required=False)


@pytest.mark.parametrize("field,value", [
    ("customer_id", 0), ("customer_id", -1), ("customer_id", True), ("customer_id", "1 OR 1=1"),
    ("customer_id", " 1"), ("customer_id", 1.0), ("customer_id", 2**64),
    ("gpu_model_id", None), ("gpu_model_id", "1.0"), ("gpu_model_id", "-1"),
    ("gpu_model_id", "１"), ("gpu_model_id", "1;DROP TABLE gpus"),
    ("gpu_count", 0), ("gpu_count", -1), ("gpu_count", True), ("gpu_count", 1.0),
    ("gpu_count", "2"), ("gpu_count", 2**31),
    ("same_server_required", 2), ("same_server_required", -1), ("same_server_required", "false"),
    ("same_server_required", 0.0), ("same_server_required", None),
    ("title", ""), ("title", " \t\n"), ("title", "가" * 201), ("title", None),
    ("original_request", ""), ("original_request", " \n"), ("original_request", None),
    ("original_request", "가" * 21846), ("original_request", "😀" * 16384),
    ("title", "\ud800"), ("original_request", "\ud800"),
])
def test_invalid_input_never_opens_transaction(field, value):
    connection = Mock(in_transaction=False)
    values = dict(VALID, **{field: value})
    with pytest.raises(InvalidJobInput):
        JobService(connection).register(**values)
    connection.start_transaction.assert_not_called()
    connection.cursor.assert_not_called()


@pytest.mark.parametrize("flag", [False, True, 0, 1])
def test_valid_boundaries_preserve_originals(flag):
    values = dict(VALID, customer_id="001", gpu_model_id=str(2**64-1), title="가" * 200,
                  original_request="가" * 21845, gpu_count=2**31-1, same_server_required=flag)
    result = validate_registration(**values)
    assert result == (1, values["title"], values["original_request"], 2**64-1, 2**31-1, int(flag))


def service_case():
    cn = Mock(in_transaction=False)
    service = JobService(cn)
    service.repository = Mock()
    service.repository.insert_request.return_value = 987
    return cn, service


def test_atomic_success_uses_generated_id_and_preserves_explicit_conditions():
    cn, service = service_case()
    assert service.register(**VALID) == 987
    service.repository.insert_request.assert_called_once_with(1, VALID["title"], VALID["original_request"])
    service.repository.insert_requirement.assert_called_once_with(987, 2, 3, 0)
    cn.start_transaction.assert_called_once_with(isolation_level="READ COMMITTED")
    cn.commit.assert_called_once()
    cn.rollback.assert_not_called()


@pytest.mark.parametrize("stage", ["require_customer", "require_model", "insert_request", "insert_requirement"])
def test_every_repository_failure_rolls_back(stage):
    cn, service = service_case()
    getattr(service.repository, stage).side_effect = RuntimeError("injected")
    with pytest.raises(RuntimeError):
        service.register(**VALID)
    cn.rollback.assert_called_once()
    cn.commit.assert_not_called()


@pytest.mark.parametrize("stage", ["start_transaction", "commit"])
def test_transaction_error_is_sanitized_and_rolled_back(stage):
    cn, service = service_case()
    getattr(cn, stage).side_effect = RuntimeError("private SQL and credentials")
    with pytest.raises(DatabaseOperationError) as exc:
        service.register(**VALID)
    assert "private" not in str(exc.value)
    cn.rollback.assert_called_once()


def test_existing_transaction_is_never_committed_or_rolled_back():
    cn, service = service_case()
    cn.in_transaction = True
    with pytest.raises(TransactionInProgress):
        service.register(**VALID)
    cn.commit.assert_not_called()
    cn.rollback.assert_not_called()
