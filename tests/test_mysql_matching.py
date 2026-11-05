"""Live MySQL matching: seed stays read-only; private data always rolls back."""

from contextlib import closing
from itertools import product

import pytest

from app.matching import JobNotFound, MissingJobRequirement
from app.repositories.hardware import HardwareRepository
from app.services.hardware import check_hardware
from mysql_audit import snapshot


pytestmark = pytest.mark.mysql


def insert(db, sql, params):
    with closing(db.cursor()) as cursor:
        cursor.execute(sql, params)
        return cursor.lastrowid


def add_requirement(db, sample, count, same, model=None):
    insert(db, "INSERT INTO job_requirements (job_request_id, gpu_model_id, gpu_count, same_server_required) "
           "VALUES (%s, %s, %s, %s)", (sample["job"], model or sample["model"], count, int(same)))


@pytest.mark.parametrize("job,model,count,same,total,maximum,status,excluded", [
    (1, "H100-SXM-80GB", 2, True, 5, 3, "HARDWARE_FEASIBLE", {4}),
    (2, "H100-SXM-80GB", 4, False, 5, 3, "HARDWARE_FEASIBLE", {4}),
    (3, "H100-SXM-80GB", 4, True, 5, 3, "HARDWARE_INSUFFICIENT", {4}),
    (4, "A100-SXM-80GB", 1, False, 2, 2, "HARDWARE_FEASIBLE", {9, 10}),
    (5, "A100-SXM-80GB", 3, False, 2, 2, "HARDWARE_INSUFFICIENT", {9, 10}),
])
def test_actual_seed_jobs(db, job, model, count, same, total, maximum, status, excluded):
    before = snapshot(db)
    result = check_hardware(HardwareRepository(db), job)
    assert result.status == status
    assert (result.requirement.model_name, result.requirement.gpu_count,
            result.requirement.same_server_required) == (model, count, same)
    assert (result.eligible_gpu_count, result.max_gpus_per_server) == (total, maximum)
    assert {gpu.gpu_id for gpu in result.model_gpus} - {gpu.gpu_id for gpu in result.candidate_gpus} == excluded
    assert snapshot(db) == before


@pytest.mark.parametrize("hardware,operational", list(product(
    ["HEALTHY", "FAULTY", "MAINTENANCE"], ["ONLINE", "MAINTENANCE", "OFFLINE"])))
def test_status_combinations_on_private_equipment(db, sample, hardware, operational):
    server = insert(db, "INSERT INTO servers (server_name, operational_status) VALUES (%s, %s)",
                    (sample["token"] + "extra", operational))
    insert(db, "INSERT INTO gpus (asset_tag, gpu_model_id, server_id, hardware_status) VALUES (%s, %s, %s, %s)",
           (sample["token"] + "extra", sample["model"], server, hardware))
    add_requirement(db, sample, 2, False)
    result = check_hardware(HardwareRepository(db), sample["job"])
    eligible = hardware == "HEALTHY" and operational == "ONLINE"
    assert result.eligible_gpu_count == 1 + int(eligible)
    assert result.status == ("HARDWARE_FEASIBLE" if eligible else "HARDWARE_INSUFFICIENT")


@pytest.mark.parametrize("count,same,status", [
    (2, True, "HARDWARE_FEASIBLE"), (3, True, "HARDWARE_INSUFFICIENT"),
    (3, False, "HARDWARE_FEASIBLE"), (4, False, "HARDWARE_FEASIBLE"),
    (5, False, "HARDWARE_INSUFFICIENT"), (1, False, "HARDWARE_FEASIBLE"),
])
def test_same_server_and_distributed_private_inventory(db, sample, count, same, status):
    second = insert(db, "INSERT INTO servers (server_name) VALUES (%s)", (sample["token"] + "second",))
    for number, server in enumerate([sample["server"], second, second]):
        insert(db, "INSERT INTO gpus (asset_tag, gpu_model_id, server_id) VALUES (%s, %s, %s)",
               (sample["token"] + str(number), sample["model"], server))
    add_requirement(db, sample, count, same)
    result = check_hardware(HardwareRepository(db), sample["job"])
    assert (result.eligible_gpu_count, result.max_gpus_per_server) == (4, 2)
    assert result.status == status
    if same and count == 3:
        assert result.reason_codes == ("SAME_SERVER_SHORTAGE",)


def test_model_mismatch_does_not_use_other_models(db, sample):
    requested_model = insert(db, "INSERT INTO gpu_models (manufacturer, model_name, memory_gb) VALUES (%s, %s, %s)",
                             ("TEST", sample["token"] + "absent", 80))
    add_requirement(db, sample, 1, False, requested_model)
    result = check_hardware(HardwareRepository(db), sample["job"])
    assert result.status == "HARDWARE_INSUFFICIENT"
    assert result.reason_codes == ("MODEL_UNAVAILABLE", "QUANTITY_SHORTAGE")
    assert result.model_gpus == result.candidate_gpus == ()


def test_missing_job_is_an_error_and_changes_nothing(db):
    missing = 18446744073709551615
    before = snapshot(db)
    with pytest.raises(JobNotFound):
        check_hardware(HardwareRepository(db), missing)
    assert snapshot(db) == before


def test_missing_original_requirement_is_a_separate_error(db, sample):
    before = snapshot(db)
    with pytest.raises(MissingJobRequirement):
        check_hardware(HardwareRepository(db), sample["job"])
    assert snapshot(db) == before


def test_queries_work_with_select_only_connection_and_do_not_end_callers_transaction(db, sample):
    add_requirement(db, sample, 1, True)
    statements = []

    class SelectOnlyCursor:
        def __init__(self):
            self.cursor = db.cursor()

        def execute(self, sql, params):
            assert sql.startswith("SELECT ")
            statements.append(sql)
            self.cursor.execute(sql, params)

        def fetchone(self):
            return self.cursor.fetchone()

        def fetchall(self):
            return self.cursor.fetchall()

        def close(self):
            self.cursor.close()

    class SelectOnlyConnection:
        # No commit/rollback or write methods: repository cannot end the
        # caller's transaction (which contains this test's uncommitted rows).
        def cursor(self):
            return SelectOnlyCursor()

    before = snapshot(db)
    assert check_hardware(HardwareRepository(SelectOnlyConnection()), sample["job"]).status == "HARDWARE_FEASIBLE"
    assert len(statements) == 2
    assert snapshot(db) == before
