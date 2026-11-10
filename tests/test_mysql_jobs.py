"""New originals remain inside rollback-only transactions, with all triggers active."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from threading import Barrier
import json
import os
import subprocess
import sys

import mysql.connector
import pytest

from allocation_helpers import RollbackEnvelope, execute
from app.db import DatabaseOperationError, connect_database
from app import job_cli
from app.job import CustomerNotFound, GPUModelNotFound, InvalidJobInput
from app.matching import JobNotFound, MissingJobRequirement
from app.repositories.hardware import HardwareRepository
from app.services.allocations import AllocationService
from app.services.hardware import check_hardware
from app.services.jobs import JobService
from app.services.proposals import ProposalService
from mysql_audit import snapshot


pytestmark = pytest.mark.mysql


def register(service, sample, **overrides):
    values = dict(customer_id=sample["customer"], title="  신규 학습  ",
                  original_request="  A100 999개가 필요하다는 원문\n",
                  gpu_model_id=sample["model"], gpu_count=1, same_server_required=True)
    values.update(overrides)
    return service.register(**values)


@pytest.mark.parametrize("count,same", [(1, True), (2, False), (2147483647, 0)])
def test_registration_exact_parent_child_detail_and_matching(db, sample, count, same):
    service = JobService(RollbackEnvelope(db))
    job = register(service, sample, gpu_count=count, same_server_required=same)
    detail = service.detail(job)
    assert detail.job_request_id == job and detail.customer_id == sample["customer"]
    assert detail.title == "  신규 학습  " and detail.original_request == "  A100 999개가 필요하다는 원문\n"
    assert (detail.gpu_model_id, detail.gpu_count, detail.same_server_required) == (sample["model"], count, bool(same))
    assert execute(db, "SELECT job_request_id FROM job_requirements WHERE job_request_id=%s", (job,)) == [(job,)]
    result = check_hardware(HardwareRepository(db), job)
    assert result.requirement.gpu_count == count
    assert result.status == ("HARDWARE_FEASIBLE" if count == 1 else "HARDWARE_INSUFFICIENT")


def test_multiple_customers_models_and_duplicate_submissions(db, sample):
    service = JobService(RollbackEnvelope(db))
    customer = execute(db, "INSERT INTO customers(display_name,email) VALUES (%s,%s)",
                       ("other", sample["token"] + "other@example.invalid"))
    model = execute(db, "INSERT INTO gpu_models(manufacturer,model_name,memory_gb) VALUES (%s,%s,%s)",
                    ("OTHER", sample["token"], 40))
    first, second = register(service, sample), register(service, sample)
    third = register(service, sample, customer_id=customer, gpu_model_id=model, gpu_count=4)
    assert len({first, second, third}) == 3
    # The sample fixture's intentionally incomplete Job is not included in these queries.
    execute(db, "INSERT INTO job_requirements VALUES (%s,%s,%s,%s)", (sample["job"], sample["model"], 1, 0))
    assert [r.job_request_id for r in service.list_for_customer(sample["customer"])] == [sample["job"], first, second]
    assert [r.job_request_id for r in service.list_for_customer(customer)] == [third]
    assert service.detail(third).gpu_model_id == model


@pytest.mark.parametrize("field,error", [("customer_id", CustomerNotFound), ("gpu_model_id", GPUModelNotFound)])
def test_missing_parent_is_clear_and_changes_nothing(db, sample, field, error):
    before = snapshot(db, "v0.3-2")
    with pytest.raises(error):
        register(JobService(RollbackEnvelope(db)), sample, **{field: 2**64-1})
    assert snapshot(db, "v0.3-2") == before


def test_second_insert_actual_fk_failure_rolls_back_first_insert(db, sample, monkeypatch):
    service = JobService(RollbackEnvelope(db))
    original = service.repository.insert_requirement
    monkeypatch.setattr(service.repository, "insert_requirement",
                        lambda job, model, count, same: original(job, 2**64-1, count, same))
    before = snapshot(db, "v0.3-2")
    with pytest.raises(DatabaseOperationError) as error:
        register(service, sample)
    assert error.value.errno in (1216, 1452)
    assert snapshot(db, "v0.3-2") == before


@pytest.mark.parametrize("table", ["job_requests", "job_requirements"])
@pytest.mark.parametrize("operation", ["UPDATE", "DELETE"])
def test_new_originals_are_protected_by_existing_triggers(db, sample, table, operation):
    job = register(JobService(RollbackEnvelope(db)), sample)
    before = snapshot(db, "v0.3-2")
    sql = (f"UPDATE {table} SET job_request_id=job_request_id WHERE job_request_id=%s" if operation == "UPDATE"
           else f"DELETE FROM {table} WHERE job_request_id=%s")
    with pytest.raises(mysql.connector.Error) as error:
        execute(db, sql, (job,))
    assert error.value.sqlstate == "45000"
    assert snapshot(db, "v0.3-2") == before


def test_new_job_original_allocation_path(db, sample):
    envelope = RollbackEnvelope(db)
    job = register(JobService(envelope), sample)
    assert ProposalService(envelope).generate(job).status == "ORIGINAL_AVAILABLE"
    allocations = AllocationService(envelope)
    allocated = allocations.allocate(job)
    assert allocated.basis.basis_type == "ORIGINAL"
    assert allocated.job_request_id == job and len(allocated.gpus) == 1
    allocations.finish(allocated.allocation_id, "COMPLETED")


def test_new_job_proposal_consent_and_accepted_allocation_path(db, sample):
    envelope = RollbackEnvelope(db)
    job = register(JobService(envelope), sample, gpu_count=2)
    proposals = ProposalService(envelope)
    proposal = proposals.generate(job).proposals[0]
    assert proposal.proposal_type == "REDUCED_QUANTITY" and proposal.gpu_count == 1
    response = proposals.respond(job, proposal.proposal_id, sample["customer"], "ACCEPTED")
    allocations = AllocationService(envelope)
    allocated = allocations.allocate_proposal(job, proposal.proposal_id, sample["customer"])
    assert allocated.basis.response_id == response.response_id
    assert allocated.basis.basis_type == "ACCEPTED_PROPOSAL"
    assert JobService(envelope).detail(job).gpu_count == 2
    allocations.finish(allocated.allocation_id, "COMPLETED")


def test_injection_text_is_preserved_as_data(db, sample):
    service = JobService(RollbackEnvelope(db))
    payload = "'); DROP TABLE job_requests; -- 한글 😀"
    job = register(service, sample, title=payload, original_request=payload)
    assert service.detail(job).original_request == payload
    assert service.detail(job).title == payload
    assert execute(db, "SELECT COUNT(*) FROM customers")[0][0] >= 3


def test_missing_job_and_incomplete_original_and_empty_customer(db, sample):
    service = JobService(RollbackEnvelope(db))
    with pytest.raises(JobNotFound): service.detail(2**64-1)
    with pytest.raises(MissingJobRequirement): service.detail(sample["job"])
    with pytest.raises(CustomerNotFound): service.list_for_customer(2**64-1)
    customer = execute(db, "INSERT INTO customers(display_name,email) VALUES (%s,%s)",
                       ("empty", sample["token"] + "empty@example.invalid"))
    assert service.list_for_customer(customer) == ()


class RollbackAtCommit:
    """Exercise real BEGIN and service COMMIT boundary, then undo immutable originals.

    Deliberately never commit: committed originals cannot be cleaned up safely.
    """
    def __init__(self, cn, barrier=None):
        self.cn, self.barrier = cn, barrier
        self.ids = []

    @property
    def in_transaction(self): return self.cn.in_transaction
    def cursor(self): return self.cn.cursor()
    def start_transaction(self, **settings): self.cn.start_transaction(**settings)
    def rollback(self): self.cn.rollback()
    def commit(self):
        # Verify both rows exist on the real connection before rollback.
        self.ids = execute(self.cn, "SELECT j.job_request_id FROM job_requests j JOIN job_requirements r "
                           "ON j.job_request_id=r.job_request_id WHERE j.title=%s", ("concurrent private job",))
        if self.barrier: self.barrier.wait(timeout=10)
        self.cn.rollback()


def test_simultaneous_real_transactions_get_distinct_ids_without_persistent_jobs(mysql_connection):
    before = snapshot(mysql_connection, "v0.3-2")
    mysql_connection.rollback()
    barrier = Barrier(2)
    def worker():
        with closing(connect_database("TEST_MYSQL_")) as cn:
            wrapper = RollbackAtCommit(cn, barrier)
            job = JobService(wrapper).register(1, "concurrent private job", "원문", 1, 1, False)
            assert (job,) in wrapper.ids
            return job
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: worker(), range(2)))
    assert results[0] != results[1]
    assert snapshot(mysql_connection, "v0.3-2") == before


def test_real_transaction_second_insert_error_leaves_no_parent(mysql_connection, monkeypatch):
    before = snapshot(mysql_connection, "v0.3-2")
    mysql_connection.rollback()
    with closing(connect_database("TEST_MYSQL_")) as cn:
        service = JobService(cn)
        original = service.repository.insert_requirement
        monkeypatch.setattr(service.repository, "insert_requirement",
                            lambda job, model, count, same: original(job, model, 0, same))
        with pytest.raises(DatabaseOperationError) as error:
            service.register(1, "rollback private job", "원문", 1, 1, False)
        assert error.value.errno == 3819 and not cn.in_transaction
    assert snapshot(mysql_connection, "v0.3-2") == before


def test_actual_registration_cli_uses_real_sql_inside_rollback_envelope(db, sample, monkeypatch, capsys):
    connection = RollbackEnvelope(db)
    connection.close = lambda: None  # The fixture owns the real connection.
    monkeypatch.setattr(job_cli, "connect_database", lambda prefix: connection if prefix == "TEST_MYSQL_" else None)
    assert job_cli.main(["--test", "register", "--customer-id", str(sample["customer"]),
                         "--title", "CLI job", "--original-request", "  CLI 원문\n",
                         "--gpu-model-id", str(sample["model"]), "--gpu-count", "1",
                         "--same-server-required", "0"]) == 0
    job = json.loads(capsys.readouterr().out)["job_request_id"]
    assert JobService(connection).detail(job).original_request == "  CLI 원문\n"
    assert check_hardware(HardwareRepository(db), job).status == "HARDWARE_FEASIBLE"
    allocated = AllocationService(connection).allocate(job)
    assert allocated.basis.basis_type == "ORIGINAL"


@pytest.mark.parametrize("operation", ["detail", "list"])
def test_read_cli_real_subprocess_is_readonly(mysql_connection, operation):
    before = snapshot(mysql_connection, "v0.3-2")
    mysql_connection.rollback()
    result = subprocess.run([sys.executable, "-m", "app.job_cli", "--test", operation, "1"],
                            capture_output=True, text=True, encoding="utf-8", timeout=15,
                            env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert (data if operation == "detail" else data["jobs"][0])["job_request_id"] == 1
    assert snapshot(mysql_connection, "v0.3-2") == before


@pytest.mark.parametrize("field,value", [("gpu_count", 0), ("gpu_count", -1), ("gpu_count", 2**31),
    ("same_server_required", 2), ("same_server_required", "false"), ("title", " "),
    ("title", "가" * 201), ("original_request", ""), ("original_request", "😀" * 16384),
    ("customer_id", "1 OR 1=1"), ("gpu_model_id", True)])
def test_validation_against_mysql_changes_no_rows(db, sample, field, value):
    before = snapshot(db, "v0.3-2")
    with pytest.raises(InvalidJobInput):
        register(JobService(RollbackEnvelope(db)), sample, **{field: value})
    assert snapshot(db, "v0.3-2") == before


def test_mysql_utf8_length_boundaries_round_trip(db, sample):
    service = JobService(RollbackEnvelope(db))
    job = register(service, sample, title="😀" * 200, original_request="가" * 21845)
    detail = service.detail(job)
    assert detail.title == "😀" * 200 and detail.original_request == "가" * 21845


@pytest.mark.parametrize("parent", ["customer", "model"])
def test_database_fk_remains_final_guard_after_precheck(db, sample, parent, monkeypatch):
    service = JobService(RollbackEnvelope(db))
    monkeypatch.setattr(service.repository, "require_" + parent, lambda identifier: None)
    before = snapshot(db, "v0.3-2")
    field = "customer_id" if parent == "customer" else "gpu_model_id"
    with pytest.raises(DatabaseOperationError) as error:
        register(service, sample, **{field: 2**64-1})
    assert error.value.errno in (1216, 1452)
    assert snapshot(db, "v0.3-2") == before
