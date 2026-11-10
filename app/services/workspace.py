"""Read view composition. Business decisions stay in the existing services."""

from app.matching import JobRequirement
from app.proposal import CustomerMismatch, ProposalConflict
from app.repositories.allocations import AllocationRepository
from app.repositories.hardware import HardwareRepository
from app.repositories.workspace import WorkspaceRepository
from app.services.allocations import AllocationService, evaluate_availability, get_availability
from app.services.hardware import check_hardware
from app.services.jobs import JobService
from app.services.proposals import ProposalService


class WorkspaceService:
    def __init__(self, connection, *, advanced):
        self.connection = connection
        self.advanced = advanced
        self.catalog = WorkspaceRepository(connection)
        self.jobs = JobService(connection)
        self.allocations = AllocationService(connection)
        self.proposals = ProposalService(connection)

    def owned_job(self, job_id, customer_id):
        job = self.jobs.detail(job_id)
        if job.customer_id != customer_id:
            raise CustomerMismatch("선택한 고객의 Job이 아닙니다. 고객 선택을 확인하세요.")
        return job

    def history(self, job_id):
        return tuple(self.allocations.detail(item.allocation_id)
                     for item in self.allocations.repository.history_for_job(job_id))

    def dashboard(self, customer_id):
        repository = AllocationRepository(self.connection)
        models = []
        for model in self.catalog.models():
            inventory = repository.list_model_gpus(model["gpu_model_id"])
            requirement = JobRequirement(0, model["gpu_model_id"], model["manufacturer"], model["model_name"], 1, False)
            availability = evaluate_availability(requirement, inventory,
                                                repository.occupied_gpu_ids(model["gpu_model_id"]))
            models.append(dict(model, total=len(inventory),
                               healthy=sum(gpu.hardware_status == "HEALTHY" for gpu in inventory),
                               available=availability.available_gpu_count))
        jobs = []
        for job in self.jobs.list_for_customer(customer_id) if customer_id is not None else ():
            history = self.history(job.job_request_id)
            proposals = self.proposals.list_for_job(job.job_request_id) if self.advanced else ()
            status = history[-1].status if history else (
                "ACCEPTED" if any(item.response and item.response.decision == "ACCEPTED" for item in proposals)
                else "PROPOSED" if proposals else "REGISTERED")
            jobs.append(dict(job=job, status=status))
        return dict(models=models, servers=self.catalog.servers(), jobs=jobs)

    def detail(self, job_id, customer_id):
        job = self.owned_job(job_id, customer_id)
        return dict(job=job, hardware=check_hardware(HardwareRepository(self.connection), job_id),
                    availability=get_availability(AllocationRepository(self.connection), job_id),
                    proposals=self.proposals.list_for_job(job_id) if self.advanced else (),
                    history=self.history(job_id))

    def proposal(self, job_id, proposal_id, customer_id):
        job = self.owned_job(job_id, customer_id)
        detail = self.proposals.detail(proposal_id)
        if detail.proposal.job_request_id != job.job_request_id:
            raise ProposalConflict("해당 대안이 이 Job에 속하지 않습니다.")
        return dict(job=job, detail=detail)

    def owned_allocation(self, job_id, allocation_id, customer_id):
        self.owned_job(job_id, customer_id)
        allocation = self.allocations.detail(allocation_id)
        if allocation.job_request_id != job_id:
            raise ProposalConflict("해당 할당이 이 Job에 속하지 않습니다.")
        return allocation
