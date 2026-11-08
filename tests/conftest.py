"""Integration tests never create, truncate or delete tables or databases."""

from contextlib import closing
from pathlib import Path
import sys
import tempfile
from uuid import uuid4

import pytest
from dotenv import load_dotenv

from app.db import PROJECT_ROOT, connect_database, connection_settings, require_supported_mysql
from database.inspect_schema import definitions, inspect, require_matching_tables


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config):
    """Use a fresh workspace temp path on Windows; never reuse/delete user paths."""
    if sys.platform != "win32" or config.option.basetemp is not None:
        return
    parent = PROJECT_ROOT / ".pytest-tmp"
    if not parent.resolve().is_relative_to(PROJECT_ROOT):
        raise pytest.UsageError("Project pytest temporary directory must remain inside the repository")
    parent.mkdir(exist_ok=True)
    # pytest clears an existing --basetemp. Reserve a NEW private parent and give
    # it a child that does not yet exist, so no earlier directory is cleared.
    run = Path(tempfile.mkdtemp(prefix="run-", dir=parent))
    config.option.basetemp = str(run / "tmp")


def pytest_addoption(parser):
    parser.addoption("--run-mysql", action="store_true", default=False,
                     help="Run integration tests using TEST_MYSQL_* (writes are rolled back)")


def pytest_collection_modifyitems(config, items):
    if not config.getoption("--run-mysql"):
        for item in items:
            if "mysql" in item.keywords:
                item.add_marker(pytest.mark.skip(reason="MySQL integration disabled; use --run-mysql"))


@pytest.fixture(scope="session")
def mysql_connection():
    load_dotenv(PROJECT_ROOT / ".env", override=False)
    # Explicitly enabled tests fail on bad/missing configuration; never silently skip.
    connection_settings("TEST_MYSQL_")
    try:
        connection = connect_database("TEST_MYSQL_")
    except Exception:
        pytest.fail("Cannot connect to dedicated test MySQL; inspect local settings", pytrace=False)
    with closing(connection):
        with closing(connection.cursor()) as cursor:
            cursor.execute("SELECT VERSION()")
            require_supported_mysql(cursor.fetchone()[0])
            cursor.execute("SELECT TABLE_NAME, ENGINE FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE()")
            tables = cursor.fetchall()
            actual = {row[0] for row in tables}
            version = "v0.3-1" if "job_proposals" in actual else "v0.2-2" if "allocations" in actual else "v0.1"
            expected = set(definitions(version)[0])
            if actual != expected:
                pytest.fail("Test database must have a complete supported schema; inspect/migrate first", pytrace=False)
            if any(engine != "InnoDB" for _, engine in tables):
                pytest.fail("Integration tests require InnoDB rollback safety for every table", pytrace=False)
            cursor.execute("SELECT TRIGGER_NAME FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA = DATABASE()")
            triggers = {row[0] for row in cursor.fetchall()}
            expected_triggers = {f"trg_{table}_no_{operation}" for table in ("job_requests", "job_requirements")
                                 for operation in ("update", "delete")}
            if triggers != expected_triggers:
                pytest.fail("Original preservation triggers must be installed before integration tests", pytrace=False)
        report = inspect(connection)
        require_matching_tables(report)
        if not all(item["matches"] for item in report["triggers"].values()):
            pytest.fail("Original trigger definitions must match", pytrace=False)
        connection.rollback()
        yield connection


@pytest.fixture
def db(mysql_connection):
    try:
        yield mysql_connection
    finally:
        mysql_connection.rollback()


@pytest.fixture
def sample(db):
    """Each test gets private rows; no assumptions about seed IDs or existing rows."""
    token = uuid4().hex
    with closing(db.cursor()) as cursor:
        cursor.execute("INSERT INTO customers (display_name, email) VALUES (%s, %s)",
                       ("Fictional test customer", token + "@example.invalid"))
        customer = cursor.lastrowid
        cursor.execute("INSERT INTO gpu_models (manufacturer, model_name, memory_gb) VALUES (%s, %s, %s)",
                       ("TEST", token, 80))
        model = cursor.lastrowid
        cursor.execute("INSERT INTO servers (server_name) VALUES (%s)", (token,))
        server = cursor.lastrowid
        cursor.execute("INSERT INTO gpus (asset_tag, gpu_model_id, server_id) VALUES (%s, %s, %s)",
                       (token, model, server))
        gpu = cursor.lastrowid
        cursor.execute("INSERT INTO job_requests (customer_id, title, original_request) VALUES (%s, %s, %s)",
                       (customer, "Fictional training", "Keep this original request"))
        job = cursor.lastrowid
    return dict(customer=customer, model=model, server=server, gpu=gpu, job=job, token=token)
