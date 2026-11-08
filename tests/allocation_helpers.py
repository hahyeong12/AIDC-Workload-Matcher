"""Rollback envelopes for single-session tests; real commit tests use seed only."""

from contextlib import closing

import pytest

from app.services.allocations import AllocationService
from database.inspect_schema import inspect


def execute(connection, sql, params=()):
    with closing(connection.cursor()) as cursor:
        cursor.execute(sql, params)
        return cursor.fetchall() if cursor.with_rows else cursor.lastrowid


class RollbackEnvelope:
    """Translate service boundaries into savepoints inside private fixture rows.

    No private immutable Job is committed: its no-delete trigger stays intact.
    Separate concurrency tests exercise actual MySQL start/commit/rollback.
    """

    def __init__(self, connection):
        self.connection = connection
        self.in_transaction = False

    def cursor(self):
        return self.connection.cursor()

    def start_transaction(self, **settings):
        assert settings == {"isolation_level": "READ COMMITTED"}
        execute(self.connection, "SAVEPOINT allocation_test_envelope")
        self.in_transaction = True

    def commit(self):
        execute(self.connection, "RELEASE SAVEPOINT allocation_test_envelope")
        self.in_transaction = False

    def rollback(self):
        execute(self.connection, "ROLLBACK TO SAVEPOINT allocation_test_envelope")
        execute(self.connection, "RELEASE SAVEPOINT allocation_test_envelope")
        self.in_transaction = False


@pytest.fixture
def allocation_case(db, sample):
    assert inspect(db)["schema_version"] in ("v0.2-2", "v0.3-1"), "Complete allocation schema required"
    model, server = sample["model"], sample["server"]
    gpu_ids = [sample["gpu"]]
    for number in range(3):
        gpu_ids.append(execute(db, "INSERT INTO gpus (asset_tag, gpu_model_id, server_id) VALUES (%s, %s, %s)",
                               (sample["token"] + str(number), model, server)))

    def new_job(count, same=False):
        job_id = execute(db, "INSERT INTO job_requests (customer_id, title, original_request) VALUES (%s, %s, %s)",
                         (sample["customer"], "Private allocation test", "Original private test request"))
        execute(db, "INSERT INTO job_requirements (job_request_id, gpu_model_id, gpu_count, same_server_required) "
                "VALUES (%s, %s, %s, %s)", (job_id, model, count, int(same)))
        return job_id

    return {"db": db, "sample": sample, "model": model, "server": server, "gpu_ids": tuple(gpu_ids),
            "new_job": new_job, "service": AllocationService(RollbackEnvelope(db))}
