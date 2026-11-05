"""CLI read-only transaction, separate error reporting and safe diagnostics."""

import json
from unittest.mock import MagicMock

import pytest

from app import match_cli
from app.matching import JobNotFound, MissingJobRequirement
from app.services.hardware import evaluate_hardware
from test_hardware_matching import gpu, requirement


@pytest.fixture
def cli_connection(monkeypatch):
    connection = MagicMock()
    connect = MagicMock(return_value=connection)
    monkeypatch.setattr(match_cli, "connect_database", connect)
    monkeypatch.setattr(match_cli, "load_dotenv", MagicMock())
    return connection, connect


@pytest.mark.parametrize("test_database", [False, True])
@pytest.mark.parametrize("enough", [False, True])
def test_cli_reports_hardware_results_and_ends_read_only_transaction(
        monkeypatch, capsys, cli_connection, test_database, enough):
    connection, connect = cli_connection
    result = evaluate_hardware(requirement(1), [gpu(1)] if enough else [])
    monkeypatch.setattr(match_cli, "check_hardware", lambda repo, job: result)
    assert match_cli.main(["001"] + (["--test"] if test_database else [])) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == ("HARDWARE_FEASIBLE" if enough else "HARDWARE_INSUFFICIENT")
    assert payload["requirement"]["job_request_id"] == 1
    connect.assert_called_once_with("TEST_MYSQL_" if test_database else "MYSQL_")
    connection.start_transaction.assert_called_once_with(
        isolation_level="REPEATABLE READ", consistent_snapshot=True, readonly=True)
    connection.rollback.assert_called_once()
    connection.close.assert_called_once()
    connection.commit.assert_not_called()


@pytest.mark.parametrize("error", [JobNotFound("Job이 없습니다."), MissingJobRequirement("원본 요구조건이 없습니다.")])
def test_cli_matching_error_is_not_hardware_insufficiency(monkeypatch, capsys, cli_connection, error):
    def fail(repo, job):
        raise error
    monkeypatch.setattr(match_cli, "check_hardware", fail)
    assert match_cli.main(["1"]) == 2
    output = capsys.readouterr()
    assert not output.out
    assert json.loads(output.err)["error"] == error.code
    cli_connection[0].rollback.assert_called_once()
    cli_connection[0].close.assert_called_once()


def test_cli_invalid_input_never_connects(cli_connection, capsys):
    assert match_cli.main(["1 OR 1=1"]) == 2
    assert json.loads(capsys.readouterr().err)["error"] == "INVALID_JOB_ID"
    cli_connection[1].assert_not_called()


@pytest.mark.parametrize("stage", ["connect", "start", "query", "rollback"])
def test_cli_database_failure_never_prints_private_details(monkeypatch, capsys, cli_connection, stage):
    connection, connect = cli_connection
    error = RuntimeError("private-password private-host private-query")
    if stage == "connect":
        connect.side_effect = error
    elif stage == "start":
        connection.start_transaction.side_effect = error
    elif stage == "query":
        connection.cursor.return_value.execute.side_effect = error
    else:
        connection.rollback.side_effect = error
        monkeypatch.setattr(match_cli, "check_hardware", lambda repo, job: None)
    assert match_cli.main(["1"]) == 1
    assert "private-" not in capsys.readouterr().err
    connection.commit.assert_not_called()
    if stage != "connect":
        connection.rollback.assert_called_once()
        connection.close.assert_called_once()
