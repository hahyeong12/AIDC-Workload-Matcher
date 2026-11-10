"""Bound SQL for original Job insertion and queries; no UPDATE/DELETE API."""

from contextlib import closing

from app.db import database_step
from app.job import CustomerNotFound, GPUModelNotFound, JobDetail
from app.matching import JobNotFound, MissingJobRequirement


class JobRepository:
    def __init__(self, connection):
        self.connection = connection

    def query(self, sql, params, *, write=False, object_name="job_requests"):
        with database_step("job-write" if write else "job-read", object_name):
            with closing(self.connection.cursor()) as cursor:
                cursor.execute(sql, params)
                return cursor.lastrowid if write else cursor.fetchall()

    def require_customer(self, customer_id):
        if not self.query("SELECT customer_id FROM customers WHERE customer_id = %s", (customer_id,)):
            raise CustomerNotFound("존재하지 않는 고객입니다.")

    def require_model(self, model_id):
        if not self.query("SELECT gpu_model_id FROM gpu_models WHERE gpu_model_id = %s", (model_id,)):
            raise GPUModelNotFound("존재하지 않는 GPU 모델입니다.")

    def insert_request(self, customer_id, title, original_request):
        return self.query(
            "INSERT INTO job_requests (customer_id, title, original_request) VALUES (%s, %s, %s)",
            (customer_id, title, original_request), write=True)

    def insert_requirement(self, job_id, model_id, count, same):
        self.query("INSERT INTO job_requirements "
                   "(job_request_id, gpu_model_id, gpu_count, same_server_required) VALUES (%s, %s, %s, %s)",
                   (job_id, model_id, count, same), write=True, object_name="job_requirements")

    def records(self, *, job_id=None, customer_id=None):
        sql = ("SELECT j.job_request_id, j.customer_id, j.title, j.original_request, j.created_at, "
               "r.gpu_model_id, r.gpu_count, r.same_server_required FROM job_requests j "
               "LEFT JOIN job_requirements r ON r.job_request_id = j.job_request_id ")
        if job_id is not None:
            sql += "WHERE j.job_request_id = %s"
            params = (job_id,)
        else:
            sql += "WHERE j.customer_id = %s ORDER BY j.job_request_id"
            params = (customer_id,)
        rows = self.query(sql, params)
        if job_id is not None and not rows:
            raise JobNotFound("존재하지 않는 Job입니다.")
        result = []
        for row in rows:
            if row[5] is None:
                raise MissingJobRequirement("Job의 원본 요구조건이 없습니다.")
            result.append(JobDetail(*row[:-1], bool(row[-1])))
        return tuple(result)
