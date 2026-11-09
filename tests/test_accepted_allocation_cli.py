"""Accepted allocation dispatch and validation before opening a database."""

from datetime import datetime
import json
from unittest.mock import MagicMock

import pytest

from app import allocation_cli
from app.allocation import AllocationResult


def test_accepted_cli_passes_explicit_job_proposal_customer(monkeypatch, capsys):
    connection = MagicMock()
    service = MagicMock()
    service.allocate_proposal.return_value = AllocationResult(7, 3, 'ACTIVE', datetime(2026, 11, 10), None, ())
    monkeypatch.setattr(allocation_cli, 'load_dotenv', MagicMock())
    monkeypatch.setattr(allocation_cli, 'connect_database', lambda prefix: connection)
    monkeypatch.setattr(allocation_cli, 'AllocationService', lambda cn: service)
    assert allocation_cli.main(['--test', 'allocate-proposal', '003', '--proposal-id', '9', '--customer-id', '2']) == 0
    service.allocate_proposal.assert_called_once_with(3, 9, 2)
    assert json.loads(capsys.readouterr().out)['allocation_id'] == 7
    connection.close.assert_called_once()


@pytest.mark.parametrize('extra', [[], ['--proposal-id', '1'], ['--customer-id', '2'],
                                   ['--proposal-id', '0', '--customer-id', '2']])
def test_missing_or_invalid_evidence_ids_do_not_connect(monkeypatch, extra, capsys):
    connect = MagicMock()
    monkeypatch.setattr(allocation_cli, 'connect_database', connect)
    assert allocation_cli.main(['--test', 'allocate-proposal', '3', *extra]) == 2
    connect.assert_not_called()
    assert json.loads(capsys.readouterr().err)['error'] == 'INVALID_JOB_ID'
