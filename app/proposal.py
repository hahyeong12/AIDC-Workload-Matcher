"""Immutable proposal/consent results. Consent is never GPU allocation."""

from dataclasses import dataclass
from datetime import datetime

from app.allocation import ResourceAvailability
from app.matching import JobRequirement


PROPOSAL_SCOPE = (
    "제안 생성 시점의 하드웨어·가용 자원 검토 결과입니다. GPU를 예약하지 않으며 "
    "수락도 실제 배정 성공을 의미하지 않습니다. 배정 시 재검증이 필요합니다."
)
AUTHENTICATION_NOTICE = "고객 ID 일치는 업무 관계 검사이며 실제 사용자 인증이 아닙니다."


class ProposalError(ValueError):
    code = "PROPOSAL_ERROR"


class ProposalNotFound(ProposalError):
    code = "PROPOSAL_NOT_FOUND"


class ProposalConflict(ProposalError):
    code = "PROPOSAL_CONFLICT"


class CustomerMismatch(ProposalError):
    code = "CUSTOMER_MISMATCH"


class ProposalIntegrityError(ProposalError):
    code = "PROPOSAL_INTEGRITY_ERROR"


@dataclass(frozen=True)
class ConditionChange:
    field: str
    original: int | bool
    proposed: int | bool


@dataclass(frozen=True)
class ProposalDraft:
    gpu_model_id: int
    gpu_count: int
    same_server_required: bool
    proposal_type: str
    reason: str
    changes: tuple[ConditionChange, ...]
    cautions: str


@dataclass(frozen=True)
class ProposalRecord:
    proposal_id: int
    job_request_id: int
    customer_id: int
    gpu_model_id: int
    gpu_count: int
    same_server_required: bool
    proposal_type: str
    reason: str
    changes: tuple[ConditionChange, ...]
    cautions: str
    created_at: datetime
    replayed: bool = False


@dataclass(frozen=True)
class CustomerResponse:
    response_id: int
    proposal_id: int
    job_request_id: int
    customer_id: int
    decision: str
    responded_at: datetime
    replayed: bool = False
    scope: str = PROPOSAL_SCOPE
    authentication_notice: str = AUTHENTICATION_NOTICE


@dataclass(frozen=True)
class ProposalDetail:
    proposal: ProposalRecord
    original_requirement: JobRequirement
    response: CustomerResponse | None
    scope: str = PROPOSAL_SCOPE


@dataclass(frozen=True)
class ProposalGeneration:
    job_request_id: int
    original_availability: ResourceAvailability
    status: str
    proposals: tuple[ProposalRecord, ...]
    reason_codes: tuple[str, ...]
    scope: str = PROPOSAL_SCOPE


@dataclass(frozen=True)
class AcceptedProposal:
    """v0.3-2 may consume these immutable evidence IDs, then recheck resources."""
    proposal: ProposalRecord
    response: CustomerResponse
    basis_kind: str = "ACCEPTED_PROPOSAL"
    scope: str = PROPOSAL_SCOPE
