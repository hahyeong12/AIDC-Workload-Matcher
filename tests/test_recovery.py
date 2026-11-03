"""Recovery guards and diagnostics, without access to developer data."""

from copy import deepcopy
from unittest.mock import MagicMock

import pytest

from app.db import DatabaseConfigError, DatabaseOperationError, database_step
from database.initialize import initialize
from database.inspect_schema import definitions, require_matching_tables, table_signature
from database.recover import export_admin_sql, recover


@pytest.fixture
def report():
    tables, triggers = definitions()
    return {"tables": {name: {"exists": True, "matches": True, "rows": 0} for name in tables},
            "triggers": {name: {"exists": True, "matches": True} for name in triggers},
            "unexpected_tables": 0, "unexpected_triggers": 0}


def test_mysql_failure_retains_only_safe_fields():
    error = RuntimeError("password=do-not-print host=private-user@example.invalid")
    error.errno = 1419
    error.sqlstate = "HY000"
    with pytest.raises(DatabaseOperationError) as caught:
        with database_step("create-trigger", "trg_job_requests_no_update"):
            raise error
    assert str(caught.value) == "stage=create-trigger object=trg_job_requests_no_update errno=1419 SQLSTATE=HY000"
    assert "do-not-print" not in str(caught.value)
    assert not hasattr(caught.value, "original_error")


def test_malformed_error_fields_are_not_echoed():
    error = RuntimeError("private")
    error.errno = "password"
    error.sqlstate = "secret"
    assert str(DatabaseOperationError("bad\nvalue", "user@host", error)) == (
        "stage=unknown object=redacted errno=unknown SQLSTATE=unknown")


@pytest.mark.parametrize("catalog_index", range(4))
def test_nonempty_catalog_refused_before_any_create(catalog_index):
    cn = MagicMock()
    cn.cursor.return_value.fetchone.side_effect = [("8.0.46",)] + [(0,)] * catalog_index + [(1,)]
    with pytest.raises(DatabaseConfigError, match="not empty"):
        initialize(cn, tables_only=True)
    assert all(call.args[0].startswith("SELECT") for call in cn.cursor.return_value.execute.call_args_list)


def test_tables_only_is_deliberately_incomplete():
    cn = MagicMock()
    cn.cursor.return_value.fetchone.side_effect = [("8.0.46",), (0,), (0,), (0,), (0,)]
    initialize(cn, tables_only=True)
    sql = [call.args[0] for call in cn.cursor.return_value.execute.call_args_list]
    assert len([s for s in sql if s.startswith("CREATE TABLE")]) == 6
    assert not any(s.startswith(("CREATE TRIGGER", "INSERT")) for s in sql)
    cn.commit.assert_not_called()


def test_initialize_error_identifies_failed_trigger_before_seed():
    cn = MagicMock()
    cn.cursor.return_value.fetchone.side_effect = [("8.0.46",), (0,), (0,), (0,), (0,)]
    error = RuntimeError("private account and host")
    error.errno, error.sqlstate = 1419, "HY000"
    def execute(sql):
        if sql.startswith("CREATE TRIGGER"):
            raise error
    cn.cursor.return_value.execute.side_effect = execute
    with pytest.raises(DatabaseOperationError, match="create-trigger object=trg_job_requests_no_update errno=1419"):
        initialize(cn)
    assert not any(call.args[0].startswith("INSERT") for call in cn.cursor.return_value.execute.call_args_list)


@pytest.mark.parametrize("table", ["customers", "gpu_models", "servers", "gpus", "job_requests", "job_requirements"])
def test_mysql_definition_rendering_and_next_id_do_not_change_schema_match(table):
    sql = definitions()[0][table]
    rendered = sql.replace("INDEX ", "KEY ")
    rendered = rendered.replace("ON UPDATE RESTRICT ON DELETE RESTRICT", "ON DELETE RESTRICT ON UPDATE RESTRICT")
    rendered = rendered.replace("ENGINE=InnoDB", "ENGINE=InnoDB AUTO_INCREMENT=350")
    assert table_signature(sql) == table_signature(rendered)


@pytest.mark.parametrize("before,after", [
    ("VARCHAR(64)", "VARCHAR(65)"),
    ("ON DELETE RESTRICT", "ON DELETE CASCADE"),
    ("'FAULTY'", "'faulty'"),
    ("gpu_model_id, hardware_status", "hardware_status, gpu_model_id"),
    ("UNIQUE (asset_tag)", "UNIQUE (asset_tag, server_id)"),
])
def test_real_schema_drift_is_not_ignored(before, after):
    sql = definitions()[0]["gpus"]
    changed = sql.replace(before, after)
    assert changed != sql
    assert table_signature(sql) != table_signature(changed)


@pytest.mark.parametrize("case", ["missing-table", "changed-table", "changed-trigger", "extra-table", "extra-trigger"])
def test_drift_blocks_recovery_before_any_write(monkeypatch, report, case):
    if case == "missing-table":
        report["tables"]["gpus"]["exists"] = False
    elif case == "changed-table":
        report["tables"]["gpus"]["matches"] = False
    elif case == "changed-trigger":
        report["triggers"]["trg_job_requests_no_update"]["matches"] = False
    elif case == "extra-table":
        report["unexpected_tables"] = 1
    else:
        report["unexpected_triggers"] = 1
    monkeypatch.setattr("database.recover.inspect", lambda connection: report)
    cn = MagicMock()
    with pytest.raises(DatabaseConfigError):
        recover(cn, seed_if_empty=True)
    cn.cursor.assert_not_called()
    cn.commit.assert_not_called()


def test_only_missing_triggers_are_created_and_repeat_recovery_does_not_write(monkeypatch, report):
    before = deepcopy(report)
    before["triggers"]["trg_job_requests_no_update"] = {"exists": False, "matches": False}
    reports = iter([before, report, report])
    monkeypatch.setattr("database.recover.inspect", lambda connection: next(reports))
    cn = MagicMock()
    recover(cn)
    calls = cn.cursor.return_value.execute.call_args_list
    assert len(calls) == 1 and calls[0].args[0].startswith("CREATE TRIGGER trg_job_requests_no_update")
    cn.reset_mock()
    monkeypatch.setattr("database.recover.inspect", lambda connection: report)
    recover(cn)
    cn.cursor.return_value.execute.assert_not_called()


def test_permission_failure_stops_before_seeding(monkeypatch, report):
    report["triggers"]["trg_job_requests_no_update"] = {"exists": False, "matches": False}
    monkeypatch.setattr("database.recover.inspect", lambda connection: report)
    cn = MagicMock()
    error = RuntimeError("private driver message")
    error.errno, error.sqlstate = 1419, "HY000"
    cn.cursor.return_value.execute.side_effect = error
    with pytest.raises(DatabaseOperationError, match="errno=1419 SQLSTATE=HY000"):
        recover(cn, seed_if_empty=True)
    cn.start_transaction.assert_not_called()
    cn.commit.assert_not_called()


def test_existing_seed_or_customer_data_is_never_overwritten(monkeypatch, report):
    monkeypatch.setattr("database.recover.inspect", lambda connection: report)
    cn = MagicMock()
    cn.cursor.return_value.fetchone.return_value = (3,)
    with pytest.raises(DatabaseConfigError, match="existing rows"):
        recover(cn, seed_if_empty=True)
    cn.commit.assert_not_called()
    cn.rollback.assert_called()
    assert all(call.args[0].startswith("SELECT") for call in cn.cursor.return_value.execute.call_args_list)


def test_failed_recovery_seed_rolls_back_all_inserts(monkeypatch, report):
    monkeypatch.setattr("database.recover.inspect", lambda connection: report)
    cn = MagicMock()
    cn.cursor.return_value.fetchone.return_value = (0,)
    def execute(sql):
        if sql.startswith("INSERT INTO gpu_models"):
            raise RuntimeError("no private text in output")
    cn.cursor.return_value.execute.side_effect = execute
    with pytest.raises(DatabaseOperationError, match="seed-insert object=gpu_models"):
        recover(cn, seed_if_empty=True)
    cn.commit.assert_not_called()
    assert cn.rollback.call_count == 2  # End audit read, then undo failed seed.


def test_admin_export_contains_only_missing_trigger_and_no_credentials(monkeypatch, tmp_path, report):
    monkeypatch.setattr("database.recover.PROJECT_ROOT", tmp_path)
    (tmp_path / "database").mkdir()
    report["triggers"]["trg_job_requests_no_update"] = {"exists": False, "matches": False}
    cn = MagicMock()
    cn.cursor.return_value.fetchone.return_value = ("aidc_workload_matcher",)
    path = tmp_path / "database/missing.sql"
    export_admin_sql(cn, report, path)
    sql = path.read_text()
    assert sql.count("CREATE TRIGGER") == 1
    assert "`aidc_workload_matcher`.`trg_job_requests_no_update`" in sql
    assert " ON `aidc_workload_matcher`.`job_requests`" in sql
    assert all(word not in sql.upper() for word in ("SET GLOBAL", "GRANT ", "DROP ", "INSERT INTO"))
    # Safe repeat does not replace a different file.
    path.write_text("keep my file")
    with pytest.raises(DatabaseConfigError, match="exists"):
        export_admin_sql(cn, report, path)
    assert path.read_text() == "keep my file"


def test_recovery_cli_defaults_to_read_only(monkeypatch, report, capsys):
    from database.recover import main
    cn = MagicMock()
    monkeypatch.setattr("database.recover.connect_database", lambda prefix: cn)
    monkeypatch.setattr("database.recover.inspect", lambda connection: report)
    write = MagicMock()
    monkeypatch.setattr("database.recover.recover", write)
    assert main([]) == 0
    write.assert_not_called()
    cn.cursor.assert_not_called()


def test_seed_option_requires_explicit_apply(monkeypatch):
    from database.recover import main
    connect = MagicMock()
    monkeypatch.setattr("database.recover.connect_database", connect)
    with pytest.raises(SystemExit) as caught:
        main(["--seed-if-empty"])
    assert caught.value.code == 2
    connect.assert_not_called()


def test_initialization_cli_reports_safe_permission_error(monkeypatch, capsys):
    from database.initialize import main
    error = RuntimeError("private-user private-password private-host")
    error.errno, error.sqlstate = 1419, "HY000"
    def fail(connection, tables_only=False):
        with database_step("create-trigger", "trg_job_requests_no_update"):
            raise error
    monkeypatch.setattr("database.initialize.connect_database", lambda prefix: MagicMock())
    monkeypatch.setattr("database.initialize.initialize", fail)
    assert main(["--confirm-empty"]) == 1
    output = capsys.readouterr().err
    assert "stage=create-trigger object=trg_job_requests_no_update errno=1419 SQLSTATE=HY000" in output
    assert "private-" not in output


def test_seed_commit_occurs_only_after_all_empty_checks_and_inserts(monkeypatch, report):
    monkeypatch.setattr("database.recover.inspect", lambda connection: report)
    cn = MagicMock()
    cn.cursor.return_value.fetchone.return_value = (0,)
    recover(cn, seed_if_empty=True)
    cn.start_transaction.assert_called_once_with(isolation_level="SERIALIZABLE")
    cn.commit.assert_called_once()
    sql = [call.args[0] for call in cn.cursor.return_value.execute.call_args_list]
    assert len(sql) == 12
    assert all(s.startswith("SELECT COUNT(*)") for s in sql[:6])
    assert all(s.startswith("INSERT INTO") for s in sql[6:])
