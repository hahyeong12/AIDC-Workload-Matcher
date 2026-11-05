"""CLI operation selection, separate errors and secret-safe diagnostics."""

from dataclasses import asdict
from datetime import datetime
import json
from unittest.mock import MagicMock

import pytest

from app import allocation_cli
from app.allocation import AllocationGPU, AllocationResult, InsufficientResources
from app.services.allocations import evaluate_availability
from test_hardware_matching import gpu, requirement


@pytest.fixture
def connection(monkeypatch):
    connection = MagicMock()
    connect = MagicMock(return_value=connection)
    monkeypatch.setattr(allocation_cli, "connect_database", connect)
    monkeypatch.setattr(allocation_cli, "load_dotenv", MagicMock())
    return connection, connect


@pytest.mark.parametrize("test_database", [False, True])
def test_read_only_availability_uses_consistent_snapshot(connection, monkeypatch, capsys, test_database):
    result = evaluate_availability(requirement(1), [gpu(1)], [])
    monkeypatch.setattr(allocation_cli, "get_availability", lambda repo, job: result)
    assert allocation_cli.main((["--test"] if test_database else []) + ["available", "001"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "AVAILABLE"
    connection[1].assert_called_once_with("TEST_MYSQL_" if test_database else "MYSQL_")
    connection[0].start_transaction.assert_called_once_with(
        isolation_level="REPEATABLE READ", readonly=True, consistent_snapshot=True)
    connection[0].rollback.assert_called_once()
    connection[0].commit.assert_not_called()
    connection[0].close.assert_called_once()


@pytest.mark.parametrize("operation", ["allocate", "complete", "fail"])
def test_mutating_commands_call_transactional_service(connection, monkeypatch, capsys, operation):
    now = datetime(2026, 11, 5)
    record = AllocationResult(42, 1, "ACTIVE", now, None, (AllocationGPU(1, 1, now, None),))
    service = MagicMock()
    service.allocate.return_value = service.finish.return_value = record
    monkeypatch.setattr(allocation_cli, "AllocationService", lambda cn: service)
    assert allocation_cli.main(["--test", operation, "001"]) == 0
    assert json.loads(capsys.readouterr().out)["allocation_id"] == 42
    if operation == "allocate":
        service.allocate.assert_called_once_with(1)
    else:
        service.finish.assert_called_once_with(1, "FAILED" if operation == "fail" else "COMPLETED")
    connection[0].close.assert_called_once()


def test_insufficient_resources_is_not_failed_execution_record(connection, monkeypatch, capsys):
    service = MagicMock()
    service.allocate.side_effect = InsufficientResources("미할당 GPU가 부족합니다.")
    monkeypatch.setattr(allocation_cli, "AllocationService", lambda cn: service)
    assert allocation_cli.main(["--test", "allocate", "1"]) == 2
    output = capsys.readouterr()
    assert not output.out and json.loads(output.err)["error"] == "INSUFFICIENT_RESOURCES"


def test_invalid_id_never_connects(connection, capsys):
    assert allocation_cli.main(["allocate", "1 OR 1=1"]) == 2
    assert json.loads(capsys.readouterr().err)["error"] == "INVALID_JOB_ID"
    connection[1].assert_not_called()


def test_read_failure_rolls_back_and_does_not_print_driver_details(connection, monkeypatch, capsys):
    def fail(repo, job):
        raise RuntimeError("private-password private-host")
    monkeypatch.setattr(allocation_cli, "get_availability", fail)
    assert allocation_cli.main(["available", "1"]) == 1
    assert "private-" not in capsys.readouterr().err
    connection[0].rollback.assert_called_once()
    connection[0].close.assert_called_once()
