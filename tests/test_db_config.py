"""Configuration and guard tests need no live database."""

from unittest.mock import MagicMock

import pytest

from app.db import DatabaseConfigError, DatabaseOperationError, connection_settings, main, require_supported_mysql
from database.initialize import initialize


@pytest.fixture
def settings():
    return {"MYSQL_HOST": "localhost", "MYSQL_PORT": "3306", "MYSQL_DATABASE": "aidc_dev",
            "MYSQL_USER": "fixture_user", "MYSQL_PASSWORD": "fixture_only_not_a_real_secret"}


def test_requires_explicit_configuration():
    with pytest.raises(DatabaseConfigError, match="MYSQL_HOST"):
        connection_settings(environ={})


@pytest.mark.parametrize("port", ["abc", "0", "65536", "-1"])
def test_rejects_invalid_ports(settings, port):
    settings["MYSQL_PORT"] = port
    with pytest.raises(DatabaseConfigError, match="PORT"):
        connection_settings(environ=settings)


def test_connection_disables_autocommit_and_uses_strict_mode(settings):
    result = connection_settings(environ=settings)
    assert result["autocommit"] is False
    assert "STRICT_ALL_TABLES" in result["sql_mode"]
    assert result["time_zone"] == "+00:00"


def test_test_configuration_never_falls_back_to_development(settings):
    with pytest.raises(DatabaseConfigError, match="TEST_MYSQL_HOST"):
        connection_settings("TEST_MYSQL_", settings)


@pytest.mark.parametrize("database,dev_database", [("aidc_dev", "aidc_dev"), ("aidc_test", "AIDC_TEST")])
def test_rejects_unsafe_test_database(settings, database, dev_database):
    values = {"TEST_" + key: value for key, value in settings.items()}
    values.update(TEST_MYSQL_DATABASE=database, MYSQL_DATABASE=dev_database)
    with pytest.raises(DatabaseConfigError):
        connection_settings("TEST_MYSQL_", values)


def test_accepts_separate_test_database(settings):
    values = {"TEST_" + key: value for key, value in settings.items()}
    values.update(TEST_MYSQL_DATABASE="aidc_test", MYSQL_DATABASE="aidc_dev")
    assert connection_settings("TEST_MYSQL_", values)["database"] == "aidc_test"


@pytest.mark.parametrize("version", ["5.7.44", "8.0.15", "10.11.0-MariaDB", "unknown"])
def test_rejects_unsupported_servers(version):
    with pytest.raises(DatabaseConfigError):
        require_supported_mysql(version)


@pytest.mark.parametrize("version", ["8.0.16", "8.4.0", "9.0.0-commercial"])
def test_accepts_supported_mysql(version):
    require_supported_mysql(version)


def test_connection_error_does_not_expose_private_details(monkeypatch, capsys):
    def broken_connection(prefix):
        raise RuntimeError("private-connection-details")
    monkeypatch.setattr("app.db.connect_database", broken_connection)
    assert main([]) == 1
    assert "private-connection-details" not in capsys.readouterr().err


def test_initialization_refuses_nonempty_database_before_any_write():
    connection = MagicMock()
    cursor = connection.cursor.return_value
    cursor.fetchone.side_effect = [("8.4.0",), (1,)]
    with pytest.raises(DatabaseConfigError, match="not empty"):
        initialize(connection)
    assert all(call.args[0].startswith("SELECT") for call in cursor.execute.call_args_list)
    connection.commit.assert_not_called()


def test_seed_failure_rolls_back_and_never_resets_database():
    connection = MagicMock()
    cursor = connection.cursor.return_value
    cursor.fetchone.side_effect = [("8.4.0",), (0,), (0,), (0,), (0,)]
    def execute(statement):
        if statement.startswith("INSERT INTO gpu_models"):
            raise RuntimeError("seed failure")
    cursor.execute.side_effect = execute
    with pytest.raises(DatabaseOperationError, match="stage=seed-insert object=gpu_models"):
        initialize(connection)
    connection.rollback.assert_called_once()
    connection.commit.assert_not_called()
    assert not any(call.args[0].startswith(("DROP", "DELETE", "TRUNCATE", "REPLACE"))
                   for call in cursor.execute.call_args_list)
