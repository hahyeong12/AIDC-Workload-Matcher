"""Atomic original/accepted-proposal allocation and return, with evidence."""

from contextlib import contextmanager
from dataclasses import replace

from app.allocation import (
    AllocationBasis, AllocationConflict, AllocationIntegrityError, InsufficientResources,
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
        raise AllocationIntegrityError("할당 GPU 수량이 적용 조건과 다릅니다. 기존 기록을 보존하고 점검하세요.")
    if requirement.same_server_required and len({gpu.server_id for gpu in record.gpus}) != 1:
        raise AllocationIntegrityError("기존 할당의 동일 서버 조건이 위반되었습니다.")
    if record.status == "ACTIVE":
        valid = record.ended_at is None and all(gpu.released_at is None for gpu in record.gpus)
    else:
        valid = record.ended_at is not None and all(gpu.released_at == record.ended_at for gpu in record.gpus)
    if not valid:
        raise AllocationIntegrityError("할당 상태와 GPU 반환 기록이 일치하지 않습니다. 수동 점검이 필요합니다.")


class AllocationService:
    """One execution per Job; the same source replays, a different source conflicts."""

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

    def basis_for(self, original, proposal_id=None, customer_id=None, *, lock=False):
        # Lazy imports avoid the proposal service's shared transaction dependency.
        from app.repositories.proposals import ProposalRepository
        from app.services.proposals import validate_proposal
        repo = ProposalRepository(self.connection)
        owner = repo.owner(original.job_request_id)
        if proposal_id is None:
            return AllocationBasis("ORIGINAL", original.job_request_id, owner, None, None,
                                   original.gpu_model_id, original.gpu_count, original.same_server_required)
        if customer_id != owner:
            raise AllocationConflict("배정 고객이 원본 Job의 고객과 다릅니다. 고객 ID는 인증을 대신하지 않습니다.")
        proposal = repo.get_proposal(proposal_id, lock=lock)
        if proposal.job_request_id != original.job_request_id or proposal.customer_id != owner:
            raise AllocationConflict("대안의 Job·고객 관계가 원본 요청과 다릅니다.")
        validate_proposal(proposal, original)
        response = repo.response_for(proposal_id, lock=lock)
        if (response is None or response.decision != "ACCEPTED"
                or response.job_request_id != original.job_request_id or response.customer_id != owner):
            raise AllocationConflict("해당 Job 고객의 ACCEPTED 응답이 필요합니다.")
        return AllocationBasis("ACCEPTED_PROPOSAL", original.job_request_id, owner, proposal_id,
                               response.response_id, proposal.gpu_model_id, proposal.gpu_count,
                               proposal.same_server_required)

    def applied_requirement(self, record, original, *, lock=False):
        basis = record.basis
        if basis is None:
            # Pre-004 rows are original executions. No backfill or history rewrite.
            return original
        expected = self.basis_for(original, basis.proposal_id, basis.customer_id, lock=lock)
        if basis != expected or basis.job_request_id != record.job_request_id:
            raise AllocationIntegrityError("배정 근거·고객 수락·적용 조건이 일치하지 않습니다.")
        return replace(original, gpu_model_id=basis.gpu_model_id, gpu_count=basis.gpu_count,
                       same_server_required=basis.same_server_required)

    def detail(self, allocation_id):
        record = self.repository.get_allocation(parse_job_id(allocation_id))
        original = self.repository.get_requirement(record.job_request_id)
        requirement = self.applied_requirement(record, original)
        validate_allocation(record, requirement)
        # Expose explicit ORIGINAL evidence for old records without writing it.
        return record if record.basis else replace(record, basis=self.basis_for(original))

    def allocate(self, job_id):
        job_id = parse_job_id(job_id)
        with allocation_transaction(self.connection):
            requirement, inventory = self.locked_context(job_id)
            existing = self.repository.latest_for_job(job_id)
            if existing is not None:
                result = self.repository.get_allocation(existing, lock=True, replayed=True)
                if result.basis and result.basis.basis_type != "ORIGINAL":
                    raise AllocationConflict("이 Job은 이미 수락 대안으로 실행되었습니다.")
                self.applied_requirement(result, requirement)
            else:
                available = evaluate_availability(
                    requirement, inventory, self.repository.occupied_gpu_ids(requirement.gpu_model_id))
                selected = choose_gpus(available)
                allocation_id = self.repository.create(job_id, selected)
                if self.repository.has_bases():
                    self.repository.create_basis(allocation_id, self.basis_for(requirement))
                result = self.repository.get_allocation(allocation_id, lock=True)
            validate_allocation(result, requirement)
        return result

    def allocate_proposal(self, job_id, proposal_id, customer_id):
        job_id, proposal_id, customer_id = map(parse_job_id, (job_id, proposal_id, customer_id))
        with allocation_transaction(self.connection):
            if not self.repository.has_bases():
                raise AllocationConflict("수락 대안 배정에는 004 마이그레이션이 필요합니다.")
            original, inventory = self.locked_context(job_id)
            basis = self.basis_for(original, proposal_id, customer_id, lock=True)
            requirement = replace(original, gpu_model_id=basis.gpu_model_id, gpu_count=basis.gpu_count,
                                  same_server_required=basis.same_server_required)
            existing = self.repository.latest_for_job(job_id)
            if existing is not None:
                result = self.repository.get_allocation(existing, lock=True, replayed=True)
                if result.basis != basis:
                    raise AllocationConflict("이 Job에는 다른 배정 근거의 실행 이력이 있습니다.")
            else:
                available = evaluate_availability(requirement, inventory,
                    self.repository.occupied_gpu_ids(requirement.gpu_model_id))
                allocation_id = self.repository.create(job_id, choose_gpus(available))
                self.repository.create_basis(allocation_id, basis)
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
            original, _ = self.locked_context(initial.job_request_id)
            requirement = self.applied_requirement(initial, original, lock=True)
            current = self.repository.get_allocation(allocation_id, lock=True)
            if current.job_request_id != initial.job_request_id or current.basis != initial.basis:
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
