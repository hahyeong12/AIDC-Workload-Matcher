"""Additive-only migration, partial failure safety and version compatibility."""

from unittest.mock import MagicMock

import pytest

from app.db import DatabaseConfigError, DatabaseOperationError
from database import proposal_migrate as migration
from database.inspect_schema import definitions, require_matching_tables, table_signature
from test_allocation_migration import report


def partial():
    result = report("v0.3-1")
    result["schema_version"] = "partial-v0.3-1"
    result["tables"]["proposal_responses"] = {"exists": False, "matches": False, "rows": None}
    return result


def test_003_preserves_all_eight_tables_and_four_original_triggers():
    old, old_triggers = definitions("v0.2-2")
    new, new_triggers = definitions("v0.3-1")
    assert len(old) == 8 and len(new) == 10
    assert old_triggers == new_triggers and len(new_triggers) == 4
    assert all(new[name] == value for name, value in old.items())
    assert migration.preflight(report("v0.2-2")) == ("job_proposals", "proposal_responses")
    assert migration.preflight(report("v0.3-1")) == ()


@pytest.mark.parametrize("case", ["original-change", "allocation-change", "original-missing", "trigger-missing",
                                  "trigger-change", "extra-table", "extra-trigger", "proposal-change"])
def test_drift_is_refused(case):
    state = report("v0.3-1")
    if case == "original-change": state["tables"]["gpus"]["matches"] = False
    elif case == "allocation-change": state["tables"]["allocation_gpus"]["matches"] = False
    elif case == "original-missing": state["tables"]["job_requests"]["exists"] = False
    elif case.startswith("trigger-"): next(iter(state["triggers"].values()))["matches"] = False
    elif case == "extra-table": state["unexpected_tables"] = 1
    elif case == "extra-trigger": state["unexpected_triggers"] = 1
    else: state["tables"]["job_proposals"]["matches"] = False
    with pytest.raises(DatabaseConfigError): migration.preflight(state)


def test_resume_requires_ordered_empty_matching_partial_schema():
    state = partial()
    with pytest.raises(DatabaseConfigError): migration.preflight(state)
    assert migration.preflight(state, resume=True) == ("proposal_responses",)
    with pytest.raises(DatabaseConfigError, match="Partial v0.3-1"): require_matching_tables(state)
    state["tables"]["job_proposals"]["rows"] = 1
    with pytest.raises(DatabaseConfigError): migration.preflight(state, resume=True)
    with pytest.raises(DatabaseConfigError): migration.preflight(report())


@pytest.mark.parametrize("before,after", [
    ("UNIQUE (accepted_job_request_id)", "UNIQUE (response_id)"),
    ("(proposal_id, job_request_id, customer_id)", "(proposal_id, customer_id, job_request_id)"),
    ("WHEN 'ACCEPTED'", "WHEN 'REJECTED'"),
])
def test_constraints_and_conditional_acceptance_drift_are_detected(before, after):
    sql = definitions("v0.3-1")[0]["proposal_responses"]
    assert sql != sql.replace(before, after)
    assert table_signature(sql) != table_signature(sql.replace(before, after))


def fake(monkeypatch, initial, final=None):
    cn = MagicMock(in_transaction=False)
    cn.cursor.return_value.fetchone.side_effect = [(1,), (0,), (0,), (1,)]
    monkeypatch.setattr(migration, "inspect", MagicMock(side_effect=[initial, final or initial]))
    monkeypatch.setattr(migration, "base_fingerprint", lambda cn, version: "unchanged")
    return cn


@pytest.mark.parametrize("apply,initial", [(False, "v0.2-2"), (True, "v0.3-1")])
def test_dry_run_and_completed_schema_do_not_execute_ddl(monkeypatch, apply, initial):
    cn = fake(monkeypatch, report(initial))
    result = migration.migrate(cn, apply=apply)
    assert not result["applied"]
    assert all(c.args[0].startswith("SELECT ") for c in cn.cursor.return_value.execute.call_args_list)
    cn.commit.assert_not_called()


def test_apply_executes_only_two_additive_creates(monkeypatch):
    cn = fake(monkeypatch, report("v0.2-2"), report("v0.3-1"))
    assert migration.migrate(cn, apply=True)["applied"]
    writes = [c.args[0] for c in cn.cursor.return_value.execute.call_args_list if not c.args[0].startswith("SELECT ")]
    assert writes == [definitions("v0.3-1")[0][name] for name in migration.EXTENSIONS]


def test_partial_resume_creates_only_response_table(monkeypatch):
    cn = fake(monkeypatch, partial(), report("v0.3-1"))
    migration.migrate(cn, apply=True, resume=True)
    writes = [c.args[0] for c in cn.cursor.return_value.execute.call_args_list if not c.args[0].startswith("SELECT ")]
    assert writes == [definitions("v0.3-1")[0]["proposal_responses"]]


def test_ddl_error_stops_without_cleanup_or_retry(monkeypatch):
    cn = fake(monkeypatch, report("v0.2-2"))
    def execute(sql, *params):
        if sql.startswith("CREATE TABLE proposal_responses"): raise RuntimeError("private SQL error")
    cn.cursor.return_value.execute.side_effect = execute
    with pytest.raises(DatabaseOperationError): migration.migrate(cn, apply=True)
    assert not any(c.args[0].startswith(("DROP", "ALTER", "INSERT", "DELETE", "TRUNCATE"))
                   for c in cn.cursor.return_value.execute.call_args_list)


def test_development_apply_and_open_transactions_remain_blocked(monkeypatch):
    connect = MagicMock()
    monkeypatch.setattr(migration, "connect_database", connect)
    with pytest.raises(SystemExit) as result: migration.main(["--apply"])
    assert result.value.code == 2
    connect.assert_not_called()
    cn = MagicMock(in_transaction=True)
    with pytest.raises(DatabaseConfigError): migration.migrate(cn, apply=True)
    cn.cursor.assert_not_called()


def test_legacy_recovery_refuses_partial_proposal_schema(monkeypatch):
    from database import recover
    monkeypatch.setattr(recover, "inspect", lambda cn: partial())
    cn = MagicMock()
    with pytest.raises(DatabaseConfigError): recover.recover(cn)
    cn.cursor.assert_not_called()
