"""Atomic allocation/return of original requests, with conservative availability."""

from contextlib import contextmanager
from dataclasses import replace

from app.allocation import (
    AllocationConflict, AllocationIntegrityError, InsufficientResources,
    ResourceAvailability, TransactionInProgress,
)
from app.db import database_step
from app.matching import parse_job_id
from app.repositories.allocations import AllocationRepository
from app.services.hardware import evaluate_hardware


def evaluate_availability(requirement, inventory, occupied_gpu_ids):
    hardware = evaluate_hardware(requirement, inventory)
    occupied = frozenset(occupied_gpu_ids)
    free = tuple(gpu for gpu in hardware.candidate_gpus if gpu.gpu_id not in occupied)
    comparison = evaluate_hardware(requirement, free)
    reasons = tuple(code.replace("QUANTITY_SHORTAGE", "AVAILABLE_QUANTITY_SHORTAGE")
                    .replace("SAME_SERVER_SHORTAGE", "AVAILABLE_SAME_SERVER_SHORTAGE")
                    for code in comparison.reason_codes
                    if code != "MODEL_UNAVAILABLE" or not hardware.model_gpus)
    explanation = (
        f"요청 모델 {requirement.model_name}: 필요 {requirement.gpu_count}개, "
        f"ONLINE 서버의 HEALTHY 미할당 GPU {len(free)}개, "
        f"한 서버의 최대 미할당 수량 {comparison.max_gpus_per_server}개. "
        + ("동일 서버 배치가 필수입니다." if requirement.same_server_required else "분산 배치가 허용됩니다."))
    return ResourceAvailability(
        requirement, "INSUFFICIENT_RESOURCES" if reasons else "AVAILABLE",
        len(free), comparison.max_gpus_per_server,
        tuple(sorted(occupied & {gpu.gpu_id for gpu in hardware.model_gpus})),
        free, comparison.server_capacities, reasons, explanation)


def get_availability(repository, job_id):
    """Advisory reads; caller supplies a consistent snapshot when needed."""
    requirement = repository.get_requirement(parse_job_id(job_id))
    return evaluate_availability(requirement, repository.list_model_gpus(requirement.gpu_model_id),
                                 repository.occupied_gpu_ids(requirement.gpu_model_id))


def choose_gpus(availability):
    if availability.status != "AVAILABLE":
        raise InsufficientResources(availability.explanation)
    requirement = availability.requirement
    candidates = availability.available_gpus
    if requirement.same_server_required:
        server_id = next(server.server_id for server in availability.server_capacities
                         if server.healthy_gpu_count >= requirement.gpu_count)
        candidates = tuple(gpu for gpu in candidates if gpu.server_id == server_id)
    return candidates[:requirement.gpu_count]


@contextmanager
def allocation_transaction(connection):
    if connection.in_transaction:
        raise TransactionInProgress("기존 트랜잭션이 없는 전용 연결을 사용하세요.")
    try:
        # READ COMMITTED prevents a read before lock acquisition from hiding a
        # competing transaction's newly committed holds after we have waited.
        with database_step("allocation-begin"):
            connection.start_transaction(isolation_level="READ COMMITTED")
        yield
        with database_step("allocation-commit"):
            connection.commit()
    except BaseException:
        with database_step("allocation-rollback"):
            connection.rollback()
        raise


def validate_allocation(record, requirement):
    if len(record.gpus) != requirement.gpu_count:
        raise AllocationIntegrityError("할당 GPU 수량이 원본과 다릅니다. 기존 기록을 보존하고 점검하세요.")
    if requirement.same_server_required and len({gpu.server_id for gpu in record.gpus}) != 1:
        raise AllocationIntegrityError("기존 할당의 동일 서버 조건이 위반되었습니다.")
    if record.status == "ACTIVE":
        valid = record.ended_at is None and all(gpu.released_at is None for gpu in record.gpus)
    else:
        valid = record.ended_at is not None and all(gpu.released_at == record.ended_at for gpu in record.gpus)
    if not valid:
        raise AllocationIntegrityError("할당 상태와 GPU 반환 기록이 일치하지 않습니다. 수동 점검이 필요합니다.")


class AllocationService:
    """One original Job runs once; repeats return its existing active/final result."""

    def __init__(self, connection):
        self.connection = connection
        self.repository = AllocationRepository(connection)

    def locked_context(self, job_id):
        original = self.repository.get_requirement(job_id)
        self.repository.lock_request(original)
        requirement = self.repository.get_requirement(job_id)
        if requirement != original:
            raise AllocationConflict("원본 조건이 변경되었습니다. 배정을 중단합니다.")
        inventory = self.repository.lock_inventory(requirement.gpu_model_id)
        return requirement, inventory

    def allocate(self, job_id):
        job_id = parse_job_id(job_id)
        with allocation_transaction(self.connection):
            requirement, inventory = self.locked_context(job_id)
            existing = self.repository.latest_for_job(job_id)
            if existing is not None:
                result = self.repository.get_allocation(existing, lock=True, replayed=True)
            else:
                available = evaluate_availability(
                    requirement, inventory, self.repository.occupied_gpu_ids(requirement.gpu_model_id))
                selected = choose_gpus(available)
                allocation_id = self.repository.create(job_id, selected)
                result = self.repository.get_allocation(allocation_id, lock=True)
            validate_allocation(result, requirement)
        return result

    def finish(self, allocation_id, status="COMPLETED"):
        allocation_id = parse_job_id(allocation_id)
        if status not in ("COMPLETED", "FAILED"):
            raise AllocationConflict("종료 상태는 COMPLETED 또는 FAILED여야 합니다.")
        with allocation_transaction(self.connection):
            # Discover the immutable Job reference without taking a lock out of
            # order. Re-read the allocation under locks before any mutation.
            initial = self.repository.get_allocation(allocation_id)
            requirement, _ = self.locked_context(initial.job_request_id)
            current = self.repository.get_allocation(allocation_id, lock=True)
            if current.job_request_id != initial.job_request_id:
                raise AllocationConflict("할당의 원본 Job 관계가 변경되었습니다.")
            validate_allocation(current, requirement)
            if current.status != "ACTIVE":
                if current.status != status:
                    raise AllocationConflict("이미 다른 종료 상태로 확정된 할당입니다.")
                result = replace(current, replayed=True)
            else:
                released, updated = self.repository.end(allocation_id, status)
                if released != requirement.gpu_count or updated != 1:
                    raise AllocationIntegrityError("전체 GPU 반환에 실패했습니다. 변경을 롤백합니다.")
                result = self.repository.get_allocation(allocation_id, lock=True)
                validate_allocation(result, requirement)
        return result
