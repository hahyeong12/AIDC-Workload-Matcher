"""Migration guards run without a database; DDL never repairs changed objects."""

from copy import deepcopy
from unittest.mock import MagicMock

import pytest

from app.db import DatabaseConfigError, DatabaseOperationError
from database import migrate as migration
from database.inspect_schema import definitions, require_matching_tables, table_signature


def report(version="v0.1"):
    tables, triggers = definitions(version)
    return {"schema_version": version, "tables": {
        name: {"exists": True, "matches": True, "rows": 0} for name in tables},
        "triggers": {name: {"exists": True, "matches": True} for name in triggers},
        "unexpected_tables": 0, "unexpected_triggers": 0}


def partial_report():
    result = report("v0.2-2")
    result["schema_version"] = "partial-v0.2-2"
    result["tables"]["allocation_gpus"] = {"exists": False, "matches": False, "rows": None}
    return result


def test_base_sql_stays_separate_and_latest_has_exactly_eight_tables():
    base, triggers = definitions()
    latest, latest_triggers = definitions("v0.2-2")
    assert len(base) == 6 and len(latest) == 8 and len(triggers) == 4
    assert latest_triggers == triggers
    assert all(latest[name] == sql for name, sql in base.items())
    assert migration.preflight(report()) == ("allocations", "allocation_gpus")
    assert migration.preflight(report("v0.2-2")) == ()


@pytest.mark.parametrize("case", ["extra-table", "extra-trigger", "base-missing", "base-changed",
                                  "trigger-missing", "trigger-changed", "allocation-changed", "partial-data"])
def test_preflight_rejects_unexpected_states(case):
    result = report("v0.2-2")
    if case == "extra-table":
        result["unexpected_tables"] = 1
    elif case == "extra-trigger":
        result["unexpected_triggers"] = 1
    elif case == "base-missing":
        result["tables"]["gpus"]["exists"] = False
    elif case == "base-changed":
        result["tables"]["gpus"]["matches"] = False
    elif case.startswith("trigger-"):
        item = next(iter(result["triggers"].values()))
        item["matches"] = False
        item["exists"] = case != "trigger-missing"
    elif case == "allocation-changed":
        result["tables"]["allocations"]["matches"] = False
    else:
        result = partial_report()
        result["tables"]["allocations"]["rows"] = 1
    with pytest.raises(DatabaseConfigError):
        migration.preflight(result, resume=True)


def test_partial_resume_requires_explicit_mode_and_existing_empty_matching_table():
    partial = partial_report()
    with pytest.raises(DatabaseConfigError, match="Partial migration"):
        migration.preflight(partial)
    assert migration.preflight(partial, resume=True) == ("allocation_gpus",)
    with pytest.raises(DatabaseConfigError, match="Partial v0.2-2"):
        require_matching_tables(partial)
    require_matching_tables(report("v0.2-2"))


@pytest.mark.parametrize("name,before,after", [
    ("allocations", "WHEN 'ACTIVE'", "WHEN 'FAILED'"),
    ("allocations", "UNIQUE (active_job_request_id)", "UNIQUE (job_request_id, allocation_id)"),
    ("allocation_gpus", "(released_at IS NULL)", "(assigned_at IS NULL)"),
    ("allocation_gpus", "PRIMARY KEY (allocation_id, gpu_id)", "PRIMARY KEY (gpu_id)"),
])
def test_generated_column_and_uniqueness_drift_is_detected(name, before, after):
    sql = definitions("v0.2-2")[0][name]
    assert sql != sql.replace(before, after)
    assert table_signature(sql) != table_signature(sql.replace(before, after))


def fake_migration(monkeypatch, initial, final=None):
    connection = MagicMock()
    connection.in_transaction = False
    connection.cursor.return_value.fetchone.side_effect = [(1,), (0,), (0,), (1,)]
    monkeypatch.setattr(migration, "inspect", MagicMock(side_effect=[initial, final or initial]))
    monkeypatch.setattr(migration, "base_fingerprint", lambda cn: "unchanged")
    return connection


def test_dry_run_does_not_execute_any_ddl(monkeypatch):
    connection = fake_migration(monkeypatch, report())
    assert migration.migrate(connection)["pending_tables"] == ["allocations", "allocation_gpus"]
    assert all(call.args[0].startswith("SELECT ") for call in connection.cursor.return_value.execute.call_args_list)
    connection.commit.assert_not_called()


def test_apply_only_creates_missing_tables_and_never_touches_originals(monkeypatch):
    connection = fake_migration(monkeypatch, report(), report("v0.2-2"))
    assert migration.migrate(connection, apply=True)["applied"]
    sql = [call.args[0] for call in connection.cursor.return_value.execute.call_args_list]
    writes = [item for item in sql if not item.startswith("SELECT ")]
    assert len(writes) == 2 and all(item.startswith("CREATE TABLE allocation") for item in writes)
    connection.commit.assert_not_called()


def test_resume_creates_only_second_table(monkeypatch):
    connection = fake_migration(monkeypatch, partial_report(), report("v0.2-2"))
    migration.migrate(connection, apply=True, resume=True)
    writes = [call.args[0] for call in connection.cursor.return_value.execute.call_args_list
              if not call.args[0].startswith("SELECT ")]
    assert writes == [definitions("v0.2-2")[0]["allocation_gpus"]]


def test_ddl_failure_stops_without_drop_or_retry(monkeypatch):
    connection = fake_migration(monkeypatch, report())
    def execute(sql, *params):
        if sql.startswith("CREATE TABLE allocation_gpus"):
            raise RuntimeError("private-connection-details")
    connection.cursor.return_value.execute.side_effect = execute
    with pytest.raises(DatabaseOperationError, match="migration-create object=allocation_gpus"):
        migration.migrate(connection, apply=True)
    assert not any(call.args[0].startswith(("DROP", "ALTER", "DELETE", "INSERT"))
                   for call in connection.cursor.return_value.execute.call_args_list)


def test_migration_refuses_callers_open_transaction():
    connection = MagicMock(in_transaction=True)
    with pytest.raises(DatabaseConfigError, match="fresh connection"):
        migration.migrate(connection, apply=True)
    connection.cursor.assert_not_called()


def test_development_apply_is_rejected_before_connect(monkeypatch):
    connect = MagicMock()
    monkeypatch.setattr(migration, "connect_database", connect)
    with pytest.raises(SystemExit) as caught:
        migration.main(["--apply"])
    assert caught.value.code == 2
    connect.assert_not_called()


def test_changed_state_is_refused_before_any_create(monkeypatch):
    initial = deepcopy(report())
    initial["unexpected_tables"] = 1
    connection = fake_migration(monkeypatch, initial)
    with pytest.raises(DatabaseConfigError):
        migration.migrate(connection, apply=True)
    assert all(call.args[0].startswith("SELECT ") for call in connection.cursor.return_value.execute.call_args_list)


def test_generated_expression_show_create_rendering_is_normalized_without_losing_semantics():
    sql = definitions("v0.2-2")[0]["allocations"]
    rendered = sql.replace("(CASE status WHEN 'ACTIVE' THEN job_request_id ELSE NULL END)",
                           "((case status when _ascii'ACTIVE' then job_request_id else NULL end))")
    assert sql != rendered
    assert table_signature(sql) == table_signature(rendered)
    assert table_signature(sql) != table_signature(rendered.replace("'ACTIVE'", "'FAILED'"))


def test_old_recovery_does_not_attempt_to_repair_partial_allocation_migration(monkeypatch):
    from database import recover as recovery
    monkeypatch.setattr(recovery, "inspect", lambda connection: partial_report())
    connection = MagicMock()
    with pytest.raises(DatabaseConfigError, match="Partial v0.2-2"):
        recovery.recover(connection)
    connection.cursor.assert_not_called()
    connection.commit.assert_not_called()


def test_completed_migration_rerun_is_read_only(monkeypatch):
    connection = fake_migration(monkeypatch, report("v0.2-2"))
    result = migration.migrate(connection, apply=True)
    assert not result["applied"] and result["pending_tables"] == []
    assert all(call.args[0].startswith("SELECT ") for call in connection.cursor.return_value.execute.call_args_list)
