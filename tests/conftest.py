"""Integration tests never create, truncate or delete tables or databases."""

from contextlib import closing
from uuid import uuid4

import pytest
from dotenv import load_dotenv

from app.db import PROJECT_ROOT, connect_database, connection_settings, require_supported_mysql


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
            expected = {"customers", "gpu_models", "servers", "gpus", "job_requests", "job_requirements"}
            if actual != expected:
                pytest.fail("Initialize the dedicated v0.1 test database before integration tests", pytrace=False)
            if any(engine != "InnoDB" for _, engine in tables):
                pytest.fail("Integration tests require InnoDB rollback safety for every table", pytrace=False)
            cursor.execute("SELECT TRIGGER_NAME FROM information_schema.TRIGGERS WHERE TRIGGER_SCHEMA = DATABASE()")
            triggers = {row[0] for row in cursor.fetchall()}
            expected_triggers = {f"trg_{table}_no_{operation}" for table in ("job_requests", "job_requirements")
                                 for operation in ("update", "delete")}
            if triggers != expected_triggers:
                pytest.fail("Original preservation triggers must be installed before integration tests", pytrace=False)
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
