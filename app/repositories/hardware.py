"""Read original requirements and physical GPU inventory without mutation."""

from contextlib import closing

from app.db import database_step
from app.matching import GPU, JobNotFound, JobRequirement, MissingJobRequirement, parse_job_id


class HardwareRepository:
    def __init__(self, connection):
        self.connection = connection

    def get_requirement(self, job_id):
        job_id = parse_job_id(job_id)
        with database_step("read-requirement", "job_requirements"):
            with closing(self.connection.cursor()) as cursor:
                # LEFT JOIN preserves the v0.1 distinction between an absent job
                # and a job whose required child row has not been registered.
                cursor.execute(
                    "SELECT j.job_request_id, r.gpu_model_id, m.manufacturer, "
                    "m.model_name, r.gpu_count, r.same_server_required "
                    "FROM job_requests j "
                    "LEFT JOIN job_requirements r ON r.job_request_id = j.job_request_id "
                    "LEFT JOIN gpu_models m ON m.gpu_model_id = r.gpu_model_id "
                    "WHERE j.job_request_id = %s", (job_id,))
                row = cursor.fetchone()
        if row is None:
            raise JobNotFound(f"Job {job_id:03d}이 존재하지 않습니다.")
        if row[1] is None:
            raise MissingJobRequirement(f"Job {job_id:03d}의 원본 GPU 요구조건이 없습니다.")
        return JobRequirement(*row[:5], bool(row[5]))

    def list_model_gpus(self, gpu_model_id):
        with database_step("read-inventory", "gpus"):
            with closing(self.connection.cursor()) as cursor:
                # Keep excluded equipment in the inventory for explanations.
                # Eligibility and per-server grouping belong to the service.
                cursor.execute(
                    "SELECT g.gpu_id, g.asset_tag, g.gpu_model_id, g.hardware_status, "
                    "s.server_id, s.server_name, s.operational_status "
                    "FROM gpus g JOIN servers s ON s.server_id = g.server_id "
                    "WHERE g.gpu_model_id = %s ORDER BY s.server_id, g.gpu_id",
                    (gpu_model_id,))
                return tuple(GPU(*row) for row in cursor.fetchall())
