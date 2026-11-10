"""Atomic registration; query transactions are owned by the caller."""

from app.job import parse_id, validate_registration
from app.matching import parse_job_id
from app.repositories.jobs import JobRepository
from app.services.allocations import allocation_transaction


class JobService:
    def __init__(self, connection):
        self.connection = connection
        self.repository = JobRepository(connection)

    def register(self, customer_id, title, original_request, gpu_model_id,
                 gpu_count, same_server_required):
        values = validate_registration(customer_id, title, original_request, gpu_model_id,
                                       gpu_count, same_server_required)
        customer, title, original, model, count, same = values
        # Reuse the established dedicated-connection transaction boundary.
        # FK constraints also protect against parent removal after these reads.
        with allocation_transaction(self.connection):
            self.repository.require_customer(customer)
            self.repository.require_model(model)
            job_id = self.repository.insert_request(customer, title, original)
            self.repository.insert_requirement(job_id, model, count, same)
        return job_id

    def detail(self, job_id):
        return self.repository.records(job_id=parse_job_id(job_id))[0]

    def list_for_customer(self, customer_id):
        customer_id = parse_id(customer_id, "customer_id")
        self.repository.require_customer(customer_id)
        return self.repository.records(customer_id=customer_id)
