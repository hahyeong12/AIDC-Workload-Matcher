"""004 guards and preservation of every historical SQL asset."""

from unittest.mock import MagicMock

import pytest

from app.db import DatabaseConfigError
from database import basis_migrate
from database.inspect_schema import definitions
from test_allocation_migration import report


def test_004_adds_one_table_and_preserves_all_old_definitions():
    old, triggers = definitions('v0.3-1')
    new, new_triggers = definitions('v0.3-2')
    assert len(new) == 11 and new_triggers == triggers
    assert all(new[name] == sql for name, sql in old.items())
    assert basis_migrate.preflight(report('v0.3-1'))
    assert not basis_migrate.preflight(report('v0.3-2'))
    assert not any(s in new['allocation_bases'] for s in ('ALTER ', 'DROP ', 'INSERT ', 'UPDATE allocations'))


@pytest.mark.parametrize('drift', ['gpus', 'allocations', 'job_proposals', 'proposal_responses', 'allocation_bases', 'trigger', 'extra'])
def test_004_refuses_drift(drift):
    state = report('v0.3-2')
    if drift == 'trigger':
        next(iter(state['triggers'].values()))['matches'] = False
    elif drift == 'extra':
        state['unexpected_tables'] = 1
    else:
        state['tables'][drift]['matches'] = False
    with pytest.raises(DatabaseConfigError):
        basis_migrate.preflight(state)


@pytest.mark.parametrize('version', ['v0.1', 'v0.2-2'])
def test_004_requires_complete_003(version):
    with pytest.raises(DatabaseConfigError):
        basis_migrate.preflight(report(version))


def test_004_development_cli_and_function_apply_are_blocked(monkeypatch):
    connect = MagicMock()
    monkeypatch.setattr(basis_migrate, 'connect_database', connect)
    with pytest.raises(SystemExit):
        basis_migrate.main(['--apply'])
    connect.assert_not_called()
    cn = MagicMock(in_transaction=False)
    cn.cursor.return_value.fetchone.return_value = ('aidc_workload_matcher',)
    with pytest.raises(DatabaseConfigError):
        basis_migrate.migrate(cn, apply=True)
    assert not any(call.args[0].startswith('CREATE') for call in cn.cursor.return_value.execute.call_args_list)


def test_004_refuses_existing_transaction():
    cn = MagicMock(in_transaction=True)
    with pytest.raises(DatabaseConfigError):
        basis_migrate.migrate(cn, apply=True)
    cn.cursor.assert_not_called()


@pytest.mark.parametrize('apply,version', [(False, 'v0.3-1'), (True, 'v0.3-1'), (True, 'v0.3-2')])
def test_dry_run_apply_and_repeat_preserve_old_fingerprint(monkeypatch, apply, version):
    cn = MagicMock(in_transaction=False)
    cn.cursor.return_value.fetchone.side_effect = [('aidc_test',), (1,), (0,), (0,), (1,)]
    monkeypatch.setattr(basis_migrate, 'inspect', MagicMock(side_effect=[report(version), report('v0.3-2')]))
    monkeypatch.setattr(basis_migrate, 'base_fingerprint', lambda c, v: 'same')
    result = basis_migrate.migrate(cn, apply=apply)
    writes = [c.args[0] for c in cn.cursor.return_value.execute.call_args_list if c.args[0].startswith('CREATE')]
    assert result['applied'] == (apply and version == 'v0.3-1')
    assert writes == ([definitions('v0.3-2')[0]['allocation_bases']] if result['applied'] else [])
    cn.commit.assert_not_called()
