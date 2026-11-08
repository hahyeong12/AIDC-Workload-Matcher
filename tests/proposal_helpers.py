"""Private rollback fixtures and live, exact-ID cleanup for proposal tests."""

from contextlib import closing
from threading import Lock

import pytest

from allocation_helpers import RollbackEnvelope, execute
from app.db import connect_database
from app.services.proposals import ProposalService
from database.inspect_schema import inspect
from mysql_audit import snapshot


@pytest.fixture
def proposal_case(db, sample):
    assert inspect(db)["schema_version"] == "v0.3-1", "Prepare test-only 003 migration first"
    second = execute(db, "INSERT INTO servers (server_name) VALUES (%s)", (sample["token"] + "second",))
    ids = [sample["gpu"]]
    for index, server in enumerate([sample["server"], sample["server"], second, second]):
        ids.append(execute(db, "INSERT INTO gpus (asset_tag, gpu_model_id, server_id) VALUES (%s, %s, %s)",
                           (sample["token"] + str(index), sample["model"], server)))
    execute(db, "INSERT INTO job_requirements (job_request_id, gpu_model_id, gpu_count, same_server_required) "
            "VALUES (%s, %s, 4, 1)", (sample["job"], sample["model"]))

    def job(count, same=False):
        identifier = execute(db, "INSERT INTO job_requests (customer_id, title, original_request) VALUES (%s, %s, %s)",
                             (sample["customer"], "Private proposal test", "Immutable original request"))
        execute(db, "INSERT INTO job_requirements VALUES (%s, %s, %s, %s)",
                (identifier, sample["model"], count, int(same)))
        return identifier

    return dict(db=db, sample=sample, job_id=sample["job"], customer=sample["customer"],
                service=ProposalService(RollbackEnvelope(db)), new_job=job, gpu_ids=ids)


@pytest.fixture
def live_proposals(mysql_connection):
    """Commit only owned proposals for unchanged seed Jobs; restore full snapshot."""
    assert inspect(mysql_connection)["schema_version"] == "v0.3-1"
    mysql_connection.rollback()
    owned, guard = set(), Lock()
    with closing(connect_database("TEST_MYSQL_")) as admin:
        assert execute(admin, "SELECT GET_LOCK(CONCAT(DATABASE(), %s), 0)", ("_v022_seed_tests",)) == [(1,)]
        before = snapshot(admin, "v0.3-1")
        assert execute(admin, "SELECT proposal_id FROM job_proposals WHERE job_request_id IN (1,2,3,4,5)") == [], \
            "Existing proposal history is preserved; use a dedicated clean test run"
        admin.rollback()

        def track(result):
            records = result.proposals if hasattr(result, "proposals") else result
            with guard:
                owned.update(record.proposal_id if hasattr(record, "proposal_id") else record for record in records)
            return result

        def generate(job_id):
            with closing(connect_database("TEST_MYSQL_")) as cn:
                return track(ProposalService(cn).generate(job_id))

        try:
            yield dict(admin=admin, track=track, generate=generate)
        finally:
            admin.rollback()
            for identifier in sorted(owned):
                rows = execute(admin, "SELECT job_request_id FROM job_proposals WHERE proposal_id = %s", (identifier,))
                assert not rows or rows[0][0] in (1, 2, 3, 4, 5)
                execute(admin, "DELETE FROM proposal_responses WHERE proposal_id = %s", (identifier,))
                execute(admin, "DELETE FROM job_proposals WHERE proposal_id = %s", (identifier,))
            admin.commit()
            assert snapshot(admin, "v0.3-1") == before
            admin.rollback()
            execute(admin, "SELECT RELEASE_LOCK(CONCAT(DATABASE(), %s))", ("_v022_seed_tests",))
