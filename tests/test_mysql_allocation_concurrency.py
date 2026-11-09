"""Independent live connections, real commits, deterministic lock contention.

Only disposable allocations referencing unchanged seed Jobs are committed.
Their exact IDs are registered and removed in finally, preserving every row
present before the test. Immutable private Jobs are never committed/deleted.
"""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing, contextmanager
from threading import Event, Lock

import pytest

from allocation_helpers import execute
from app.allocation import InsufficientResources
from app.db import connect_database, database_step
from app.services.allocations import AllocationService
from mysql_audit import snapshot


pytestmark = pytest.mark.mysql


@contextmanager
def live_connection():
    with database_step("test-connect"), closing(connect_database("TEST_MYSQL_")) as connection:
        execute(connection, "SET SESSION innodb_lock_wait_timeout = 5")
        try:
            yield connection
        finally:
            connection.rollback()


@pytest.fixture
def committed_allocations(mysql_connection):
    """Exclusive test run, exact owned-ID cleanup, full pre/post fingerprint."""
    owned, guard = set(), Lock()
    with live_connection() as admin:
        assert execute(admin, "SELECT GET_LOCK(CONCAT(DATABASE(), %s), 0)", ("_v022_seed_tests",)) == [(1,)]
        before = snapshot(admin, "v0.2-2")
        existing = execute(admin, "SELECT allocation_id FROM allocations WHERE job_request_id IN (1, 2, 3, 4, 5)")
        assert existing == [], "Committed seed tests require no prior seed allocations; existing history will not be deleted"
        admin.rollback()

        def track(result):
            with guard:
                owned.add(result.allocation_id)
            return result

        try:
            yield track, admin
        finally:
            admin.rollback()
            # This is test-only cleanup of explicitly registered disposable rows.
            # No allocation deletion API exists in production code.
            for allocation_id in sorted(owned):
                rows = execute(admin, "SELECT job_request_id FROM allocations WHERE allocation_id = %s",
                               (allocation_id,))
                assert not rows or rows[0][0] in (1, 2, 3, 4, 5)
                if execute(admin, "SELECT TABLE_NAME FROM information_schema.TABLES "
                           "WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME='allocation_bases'"):
                    execute(admin, "DELETE FROM allocation_bases WHERE allocation_id = %s", (allocation_id,))
                execute(admin, "DELETE FROM allocation_gpus WHERE allocation_id = %s", (allocation_id,))
                execute(admin, "DELETE FROM allocations WHERE allocation_id = %s", (allocation_id,))
            admin.commit()
            assert snapshot(admin, "v0.2-2") == before
            admin.rollback()
            execute(admin, "SELECT RELEASE_LOCK(CONCAT(DATABASE(), %s))", ("_v022_seed_tests",))


def race_with_model_lock(track, first_action, second_action):
    """First holds locks; second must really wait, then observe committed state."""
    first_locked, second_attempt, second_acquired, release = (Event() for _ in range(4))

    def first_worker():
        with live_connection() as connection:
            service = AllocationService(connection)
            original = service.locked_context
            def pause(job):
                context = original(job)
                first_locked.set()
                assert release.wait(5), "Timed out waiting for test coordinator"
                return context
            service.locked_context = pause
            return track(first_action(service))

    def second_worker():
        with live_connection() as connection:
            service = AllocationService(connection)
            original = service.repository.lock_request
            def observe(requirement):
                second_attempt.set()
                original(requirement)
                second_acquired.set()
            service.repository.lock_request = observe
            try:
                return track(second_action(service))
            except InsufficientResources as error:
                return error

    with ThreadPoolExecutor(max_workers=2) as executor:
        leader = executor.submit(first_worker)
        try:
            assert first_locked.wait(3)
            follower = executor.submit(second_worker)
            assert second_attempt.wait(3)
            assert not second_acquired.wait(0.2), "Second transaction bypassed the held model lock"
        finally:
            release.set()
        results = leader.result(timeout=8), follower.result(timeout=8)
    assert second_acquired.is_set()
    return results


def test_competing_jobs_cannot_both_allocate_overlapping_gpus(committed_allocations):
    track, admin = committed_allocations
    first, second = race_with_model_lock(track, lambda service: service.allocate(1), lambda service: service.allocate(2))
    assert first.status == "ACTIVE" and [gpu.gpu_id for gpu in first.gpus] == [1, 2]
    assert isinstance(second, InsufficientResources)  # 4 requested; only 3 remain after waiting.
    assert execute(admin, "SELECT job_request_id, status FROM allocations ORDER BY allocation_id") == [(1, "ACTIVE")]
    assert execute(admin, "SELECT gpu_id FROM allocation_gpus WHERE released_at IS NULL "
                   "GROUP BY gpu_id HAVING COUNT(*) > 1") == []


def test_same_job_concurrent_repeats_return_one_allocation(committed_allocations):
    track, admin = committed_allocations
    first, second = race_with_model_lock(track, lambda service: service.allocate(1), lambda service: service.allocate(1))
    assert first.allocation_id == second.allocation_id and second.replayed
    assert execute(admin, "SELECT COUNT(*) FROM allocations WHERE job_request_id = 1") == [(1,)]
    assert execute(admin, "SELECT COUNT(*) FROM allocation_gpus WHERE allocation_id = %s", (first.allocation_id,)) == [(2,)]


def test_completion_and_allocation_wait_then_reuse_returned_gpus(committed_allocations):
    track, admin = committed_allocations
    with live_connection() as connection:
        first = track(AllocationService(connection).allocate(1))
    completed, next_allocation = race_with_model_lock(
        track, lambda service: service.finish(first.allocation_id), lambda service: service.allocate(2))
    assert completed.status == "COMPLETED"
    assert next_allocation.status == "ACTIVE"
    assert [item.gpu_id for item in next_allocation.gpus] == [1, 2, 3, 5]
    assert [item.server_id for item in next_allocation.gpus] == [1, 1, 1, 2]
    assert execute(admin, "SELECT a.status, ag.released_at IS NULL FROM allocation_gpus ag "
                   "JOIN allocations a USING (allocation_id) WHERE ag.gpu_id = 1 ORDER BY a.allocation_id") == [
                       ("COMPLETED", 0), ("ACTIVE", 1)]


def test_concurrent_completion_is_idempotent(committed_allocations):
    track, admin = committed_allocations
    with live_connection() as connection:
        allocated = track(AllocationService(connection).allocate(1))
    first, second = race_with_model_lock(
        track, lambda service: service.finish(allocated.allocation_id), lambda service: service.finish(allocated.allocation_id))
    assert first.status == second.status == "COMPLETED"
    assert first.ended_at == second.ended_at and second.replayed


def test_generated_gpu_unique_blocks_concurrent_direct_inserts(committed_allocations):
    """Prove the database guard works even without the service's model lock."""
    import mysql.connector
    from types import SimpleNamespace

    track, admin = committed_allocations
    first_inserted, second_attempt, second_finished, release = (Event() for _ in range(4))
    # Prepare both FK parents before contention. Only the generated GPU UNIQUE
    # index is under test here; header-index/FK waits are tested by service races.
    first_id = execute(admin, "INSERT INTO allocations (job_request_id) VALUES (1)")
    track(SimpleNamespace(allocation_id=first_id))
    second_id = execute(admin, "INSERT INTO allocations (job_request_id) VALUES (2)")
    track(SimpleNamespace(allocation_id=second_id))
    admin.commit()

    def first_worker():
        with live_connection() as connection:
            connection.start_transaction(isolation_level="READ COMMITTED")
            for gpu_id in (1, 2):
                execute(connection, "INSERT INTO allocation_gpus (allocation_id, gpu_id, server_id) VALUES (%s, %s, 1)",
                        (first_id, gpu_id))
            first_inserted.set()
            assert release.wait(5)
            connection.commit()
            return first_id

    def second_worker():
        with live_connection() as connection:
            connection.start_transaction(isolation_level="READ COMMITTED")
            second_attempt.set()
            try:
                execute(connection, "INSERT INTO allocation_gpus (allocation_id, gpu_id, server_id) VALUES (%s, 1, 1)",
                        (second_id,))
            except mysql.connector.Error as error:
                connection.rollback()
                return error.errno
            finally:
                second_finished.set()
            raise AssertionError("Duplicate GPU insert unexpectedly succeeded")

    with ThreadPoolExecutor(max_workers=2) as executor:
        first_future = executor.submit(first_worker)
        try:
            assert first_inserted.wait(3)
            second_future = executor.submit(second_worker)
            assert second_attempt.wait(3)
            assert not second_finished.wait(0.2)
        finally:
            release.set()
        result = first_future.result(timeout=8)
        assert second_future.result(timeout=8) == 1062
    assert result == first_id
    assert execute(admin, "SELECT COUNT(*) FROM allocation_gpus WHERE allocation_id = %s", (second_id,)) == [(0,)]


def test_real_cli_allocation_completion_reuse_and_history(committed_allocations):
    import json
    import os
    import subprocess
    import sys
    from types import SimpleNamespace

    track, _ = committed_allocations
    def cli(operation, identifier, expected_code=0):
        run = subprocess.run(
            [sys.executable, "-m", "app.allocation_cli", "--test", operation, str(identifier)],
            capture_output=True, text=True, encoding="utf-8", timeout=10,
            env=dict(os.environ, PYTHONIOENCODING="utf-8"))
        assert run.returncode == expected_code, run.stderr
        result = json.loads(run.stdout if expected_code == 0 else run.stderr)
        if operation == "allocate" and expected_code == 0:
            track(SimpleNamespace(allocation_id=result["allocation_id"]))
        return result

    assert cli("available", 1)["available_gpu_count"] == 5
    first = cli("allocate", "001")
    assert first["status"] == "ACTIVE" and [item["gpu_id"] for item in first["gpus"]] == [1, 2]
    assert cli("allocate", 1)["replayed"]
    assert cli("available", 2)["available_gpu_count"] == 3
    assert cli("allocate", 2, 2)["error"] == "INSUFFICIENT_RESOURCES"
    completed = cli("complete", first["allocation_id"])
    assert completed["status"] == "COMPLETED"
    assert cli("complete", first["allocation_id"])["ended_at"] == completed["ended_at"]
    assert cli("available", 2)["available_gpu_count"] == 5
    second = cli("allocate", 2)
    assert [item["gpu_id"] for item in second["gpus"]] == [1, 2, 3, 5]
    history = cli("history", 1)["history"]
    assert [row["status"] for row in history] == ["COMPLETED", "ACTIVE"]
    assert history[0]["released_at"] is not None and history[1]["released_at"] is None
    assert cli("fail", second["allocation_id"])["status"] == "FAILED"
    assert cli("fail", second["allocation_id"])["replayed"]
