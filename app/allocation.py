"""Allocation records and errors, separate from hardware-only feasibility."""

from dataclasses import dataclass
from datetime import datetime

from app.matching import GPU, JobRequirement, ServerCapacity


class AllocationError(RuntimeError):
    code = "ALLOCATION_ERROR"


class InsufficientResources(AllocationError):
    code = "INSUFFICIENT_RESOURCES"


class AllocationNotFound(AllocationError):
    code = "ALLOCATION_NOT_FOUND"


class AllocationConflict(AllocationError):
    code = "ALLOCATION_CONFLICT"


class AllocationIntegrityError(AllocationError):
    code = "ALLOCATION_INTEGRITY_ERROR"


class TransactionInProgress(AllocationError):
    code = "TRANSACTION_IN_PROGRESS"


@dataclass(frozen=True)
class AllocationGPU:
    gpu_id: int
    server_id: int
    assigned_at: datetime
    released_at: datetime | None


@dataclass(frozen=True)
class AllocationBasis:
    basis_type: str
    job_request_id: int
    customer_id: int
    proposal_id: int | None
    response_id: int | None
    gpu_model_id: int
    gpu_count: int
    same_server_required: bool


@dataclass(frozen=True)
class AllocationResult:
    allocation_id: int
    job_request_id: int
    status: str
    allocated_at: datetime
    ended_at: datetime | None
    gpus: tuple[AllocationGPU, ...]
    replayed: bool = False
    basis: AllocationBasis | None = None


@dataclass(frozen=True)
class ResourceAvailability:
    requirement: JobRequirement
    status: str
    available_gpu_count: int
    max_gpus_per_server: int
    occupied_gpu_ids: tuple[int, ...]
    available_gpus: tuple[GPU, ...]
    server_capacities: tuple[ServerCapacity, ...]
    reason_codes: tuple[str, ...]
    explanation: str
    scope: str = "조회 시점의 미할당 자원입니다. 실제 배정은 트랜잭션과 잠금 후 재검증합니다."
