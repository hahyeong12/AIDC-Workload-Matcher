"""CLI arguments, JSON results and safe failures without opening a database."""

from datetime import datetime
import json
from unittest.mock import Mock

import pytest

from app import job_cli
from app.db import DatabaseConfigError, DatabaseOperationError
from app.job import CustomerNotFound, JobDetail


REGISTER = ["register", "--customer-id", "1", "--title", "title", "--original-request", "original",
            "--gpu-model-id", "2", "--gpu-count", "3", "--same-server-required", "0"]


@pytest.fixture
def cli(monkeypatch):
    cn = Mock()
    connect = Mock(return_value=cn)
    service = Mock()
    service.register.return_value = 123
    record = JobDetail(123, 1, "title", "original", datetime(2026, 11, 10), 2, 3, False)
    service.detail.return_value = record
    service.list_for_customer.return_value = (record,)
    monkeypatch.setattr(job_cli, "connect_database", connect)
    monkeypatch.setattr(job_cli, "JobService", Mock(return_value=service))
    monkeypatch.setattr(job_cli, "load_dotenv", Mock())
    return cn, connect, service


@pytest.mark.parametrize("test", [False, True])
def test_registration_cli_commits_via_service_and_returns_id(cli, capsys, test):
    cn, connect, service = cli
    assert job_cli.main((["--test"] if test else []) + REGISTER) == 0
    assert json.loads(capsys.readouterr().out) == {"job_request_id": 123}
    service.register.assert_called_once_with(1, "title", "original", 2, 3, 0)
    connect.assert_called_once_with("TEST_MYSQL_" if test else "MYSQL_")
    cn.close.assert_called_once()


@pytest.mark.parametrize("operation", ["detail", "list"])
def test_read_cli_snapshot_and_json(cli, capsys, operation):
    cn, _, _ = cli
    assert job_cli.main(["--test", operation, "1"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert (result if operation == "detail" else result["jobs"][0])["gpu_count"] == 3
    cn.start_transaction.assert_called_once_with(
        isolation_level="REPEATABLE READ", readonly=True, consistent_snapshot=True)
    cn.rollback.assert_called_once()
    cn.commit.assert_not_called()


@pytest.mark.parametrize("option,value", [("--gpu-count", "0"), ("--title", " "),
                                        ("--customer-id", "1 OR 1=1"), ("--original-request", "")])
def test_invalid_values_fail_before_connect(cli, capsys, option, value):
    args = REGISTER.copy()
    args[args.index(option)+1] = value
    assert job_cli.main(args) == 2
    assert json.loads(capsys.readouterr().err)["error"] == "INVALID_JOB_INPUT"
    cli[1].assert_not_called()


@pytest.mark.parametrize("option,value", [("--same-server-required", "true"), ("--gpu-count", "1.5")])
def test_invalid_cli_formats_fail_argparse(cli, option, value):
    args = REGISTER.copy()
    args[args.index(option)+1] = value
    with pytest.raises(SystemExit) as error: job_cli.main(args)
    assert error.value.code == 2
    cli[1].assert_not_called()


@pytest.mark.parametrize("error,status", [(CustomerNotFound("missing"), 2),
    (DatabaseOperationError("job-write", "job_requests", RuntimeError("secret")), 1),
    (RuntimeError("secret SQL"), 1)])
def test_service_errors_have_safe_diagnostics(cli, capsys, error, status):
    cli[2].register.side_effect = error
    assert job_cli.main(REGISTER) == status
    output = capsys.readouterr()
    assert not output.out and "secret" not in output.err
    cli[0].close.assert_called_once()


def test_connection_config_error_is_reported(cli, capsys):
    cli[1].side_effect = DatabaseConfigError("Missing environment variables")
    assert job_cli.main(REGISTER) == 1
    assert "Missing environment variables" in capsys.readouterr().err
