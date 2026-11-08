"""CLI input, database selection, read boundaries and consent-only output."""

from datetime import datetime
import json
from unittest.mock import MagicMock

import pytest

from app import proposal_cli
from app.proposal import CustomerResponse


@pytest.fixture
def configured(monkeypatch):
    cn, service = MagicMock(), MagicMock()
    connect = MagicMock(return_value=cn)
    monkeypatch.setattr(proposal_cli, "connect_database", connect)
    monkeypatch.setattr(proposal_cli, "ProposalService", lambda connection: service)
    monkeypatch.setattr(proposal_cli, "load_dotenv", MagicMock())
    service.list_for_job.return_value = ()
    service.history.return_value = ()
    service.respond.return_value = CustomerResponse(10, 7, 3, 2, "ACCEPTED", datetime(2026, 11, 8))
    return cn, service, connect


@pytest.mark.parametrize("test", [False, True])
@pytest.mark.parametrize("operation,field", [("list", "proposals"), ("history", "responses")])
def test_read_cli_uses_readonly_snapshot_and_correct_database(configured, capsys, test, operation, field):
    cn, service, connect = configured
    assert proposal_cli.main((["--test"] if test else []) + [operation, "003"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result[field] == [] and "예약하지" in result["scope"] and "인증이 아닙니다" in result["authentication_notice"]
    connect.assert_called_once_with("TEST_MYSQL_" if test else "MYSQL_")
    cn.start_transaction.assert_called_once_with(isolation_level="REPEATABLE READ", readonly=True, consistent_snapshot=True)
    cn.rollback.assert_called_once()
    cn.commit.assert_not_called()
    cn.close.assert_called_once()


@pytest.mark.parametrize("operation,decision", [("accept", "ACCEPTED"), ("reject", "REJECTED")])
def test_consent_commands_pass_job_proposal_and_customer_without_calling_allocation(configured, capsys, operation, decision):
    _, service, _ = configured
    assert proposal_cli.main(["--test", operation, "003", "007", "002"]) == 0
    service.respond.assert_called_once_with(job_id=3, proposal_id=7, customer_id=2, decision=decision)
    result = json.loads(capsys.readouterr().out)
    assert "allocation_id" not in result and "배정 성공을 의미하지" in result["scope"]


@pytest.mark.parametrize("args", [["generate", "0"], ["show", "1 OR 1=1"], ["accept", "3", "7", "-1"]])
def test_invalid_ids_do_not_connect(configured, capsys, args):
    assert proposal_cli.main(args) == 2
    configured[2].assert_not_called()
    assert json.loads(capsys.readouterr().err)["error"] == "INVALID_JOB_ID"


def test_read_error_rolls_back_and_hides_driver_details(configured, capsys):
    cn, service, _ = configured
    service.list_for_job.side_effect = RuntimeError("password=private host=secret")
    assert proposal_cli.main(["--test", "list", "3"]) == 1
    assert "private" not in capsys.readouterr().err
    cn.rollback.assert_called_once()
    cn.close.assert_called_once()
