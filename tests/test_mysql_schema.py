"""Real InnoDB behavior; no SQLite substitute for MySQL constraints."""

from contextlib import closing

import pytest


pytestmark = pytest.mark.mysql


def execute(db, sql, params=()):
    with closing(db.cursor()) as cursor:
        cursor.execute(sql, params)
        if cursor.with_rows:
            return cursor.fetchall()


def rejected(db, sql, params, errno):
    import mysql.connector

    with pytest.raises(mysql.connector.Error) as caught:
        execute(db, sql, params)
    allowed = (errno,) if isinstance(errno, int) else errno
    assert caught.value.errno in allowed
    return caught.value


@pytest.mark.parametrize("table,key,fields", [
    ("customers", "customer", "customer_id, display_name, email"),
    ("gpu_models", "model", "gpu_model_id, manufacturer, model_name, memory_gb"),
    ("servers", "server", "server_id, server_name"),
    ("gpus", "gpu", "gpu_id, asset_tag, gpu_model_id, server_id"),
    ("job_requests", "job", "job_request_id, customer_id, title, original_request"),
    ("job_requirements", "job", "job_request_id, gpu_model_id, gpu_count, same_server_required"),
])
def test_primary_key_duplicate_rejected(db, sample, table, key, fields):
    if table == "job_requirements":
        execute(db, "INSERT INTO job_requirements VALUES (%s, %s, 1, 0)", (sample["job"], sample["model"]))
    primary = fields.split(",")[0]
    error = rejected(db, f"INSERT INTO {table} ({fields}) SELECT {fields} FROM {table} WHERE {primary} = %s",
                     (sample[key],), 1062)
    assert "PRIMARY" in error.msg


@pytest.mark.parametrize("table", ["customers", "gpu_models", "servers", "gpus"])
def test_unique_business_identifiers_rejected(db, sample, table):
    statements = {
        "customers": ("INSERT INTO customers (display_name, email) VALUES ('test', %s)",
                      (sample["token"] + "@example.invalid",)),
        "gpu_models": ("INSERT INTO gpu_models (manufacturer, model_name, memory_gb) VALUES ('TEST', %s, 80)",
                       (sample["token"],)),
        "servers": ("INSERT INTO servers (server_name) VALUES (%s)", (sample["token"],)),
        "gpus": ("INSERT INTO gpus (asset_tag, gpu_model_id, server_id) VALUES (%s, %s, %s)",
                 (sample["token"], sample["model"], sample["server"])),
    }
    sql, params = statements[table]
    error = rejected(db, sql, params, 1062)
    assert "uq_" in error.msg


@pytest.mark.parametrize("relation", ["gpu_server", "gpu_model", "job_customer", "requirement_job", "requirement_model"])
def test_foreign_key_rejects_missing_parent(db, sample, relation):
    # Delete nothing: a confirmed absent unsigned maximum value is the missing parent.
    missing = 18446744073709551615
    for table, key in [("servers", "server_id"), ("gpu_models", "gpu_model_id"),
                       ("customers", "customer_id"), ("job_requests", "job_request_id")]:
        assert execute(db, f"SELECT {key} FROM {table} WHERE {key} = %s", (missing,)) == []
    statements = {
        "gpu_server": ("INSERT INTO gpus (asset_tag, gpu_model_id, server_id) VALUES (%s, %s, %s)",
                       (sample["token"] + "x", sample["model"], missing)),
        "gpu_model": ("INSERT INTO gpus (asset_tag, gpu_model_id, server_id) VALUES (%s, %s, %s)",
                      (sample["token"] + "x", missing, sample["server"])),
        "job_customer": ("INSERT INTO job_requests (customer_id, title, original_request) VALUES (%s, %s, %s)",
                         (missing, "test", "test")),
        "requirement_job": ("INSERT INTO job_requirements VALUES (%s, %s, 1, 0)", (missing, sample["model"])),
        "requirement_model": ("INSERT INTO job_requirements VALUES (%s, %s, 1, 0)", (sample["job"], missing)),
    }
    sql, params = statements[relation]
    # MySQL may withhold constraint details for a restricted account (1216).
    # Both codes denote a missing referenced row; require the integrity SQLSTATE.
    error = rejected(db, sql, params, (1216, 1452))
    assert error.sqlstate == "23000"
    if relation.startswith("gpu_"):
        assert execute(db, "SELECT gpu_id FROM gpus WHERE asset_tag = %s", (sample["token"] + "x",)) == []
    elif relation == "job_customer":
        assert execute(db, "SELECT job_request_id FROM job_requests WHERE customer_id = %s", (missing,)) == []
    else:
        assert execute(db, "SELECT job_request_id FROM job_requirements WHERE job_request_id = %s", (params[0],)) == []


@pytest.mark.parametrize("count", [0, -1])
def test_nonpositive_gpu_count_rejected(db, sample, count):
    rejected(db, "INSERT INTO job_requirements VALUES (%s, %s, %s, 0)",
             (sample["job"], sample["model"], count), 3819)


def test_null_gpu_count_rejected(db, sample):
    rejected(db, "INSERT INTO job_requirements VALUES (%s, %s, NULL, 0)",
             (sample["job"], sample["model"]), 1048)


def test_server_gpu_and_customer_requirement_relationships(db, sample):
    execute(db, "INSERT INTO job_requirements VALUES (%s, %s, 2, 1)", (sample["job"], sample["model"]))
    assert execute(db, "SELECT s.server_id, g.gpu_model_id FROM gpus g JOIN servers s "
                   "ON s.server_id = g.server_id WHERE g.gpu_id = %s", (sample["gpu"],)) == [
                       (sample["server"], sample["model"])]
    assert execute(db, "SELECT j.customer_id, r.gpu_model_id, r.gpu_count, r.same_server_required "
                   "FROM job_requests j JOIN job_requirements r USING (job_request_id) "
                   "WHERE j.job_request_id = %s", (sample["job"],)) == [
                       (sample["customer"], sample["model"], 2, 1)]


def test_faulty_gpu_and_offline_server_are_distinct_from_healthy_hardware(db, sample):
    execute(db, "INSERT INTO gpus (asset_tag, gpu_model_id, server_id, hardware_status) "
            "VALUES (%s, %s, %s, 'FAULTY')", (sample["token"] + "f", sample["model"], sample["server"]))
    rows = execute(db, "SELECT hardware_status, COUNT(*) FROM gpus WHERE server_id = %s "
                   "GROUP BY hardware_status ORDER BY hardware_status", (sample["server"],))
    assert rows == [("FAULTY", 1), ("HEALTHY", 1)]
    execute(db, "UPDATE servers SET operational_status = 'OFFLINE' WHERE server_id = %s", (sample["server"],))
    assert execute(db, "SELECT g.gpu_id FROM gpus g JOIN servers s USING (server_id) "
                   "WHERE s.server_id = %s AND s.operational_status = 'ONLINE' "
                   "AND g.hardware_status = 'HEALTHY'", (sample["server"],)) == []


@pytest.mark.parametrize("table,column,key,value", [
    ("servers", "operational_status", "server_id", "UNKNOWN"),
    ("gpus", "hardware_status", "gpu_id", "ALLOCATED"),
])
def test_invalid_equipment_status_rejected(db, sample, table, column, key, value):
    row = sample["server"] if table == "servers" else sample["gpu"]
    rejected(db, f"UPDATE {table} SET {column} = %s WHERE {key} = %s", (value, row), 3819)


def test_invalid_placement_flag_rejected(db, sample):
    rejected(db, "INSERT INTO job_requirements VALUES (%s, %s, 1, 2)",
             (sample["job"], sample["model"]), 3819)


@pytest.mark.parametrize("table", ["job_requests", "job_requirements"])
@pytest.mark.parametrize("operation", ["update", "delete"])
def test_originals_cannot_be_overwritten_or_deleted(db, sample, table, operation):
    execute(db, "INSERT INTO job_requirements VALUES (%s, %s, 1, 0)", (sample["job"], sample["model"]))
    if operation == "update":
        assignment = "original_request = 'changed'" if table == "job_requests" else "gpu_count = 99"
        sql = f"UPDATE {table} SET {assignment} WHERE job_request_id = %s"
    else:
        sql = f"DELETE FROM {table} WHERE job_request_id = %s"
    rejected(db, sql, (sample["job"],), 1644)
    assert execute(db, "SELECT original_request FROM job_requests WHERE job_request_id = %s",
                   (sample["job"],)) == [("Keep this original request",)]
    assert execute(db, "SELECT gpu_count FROM job_requirements WHERE job_request_id = %s",
                   (sample["job"],)) == [(1,)]
