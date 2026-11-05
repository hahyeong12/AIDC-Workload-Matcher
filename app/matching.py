"""Immutable inputs, results and errors for hardware-only matching."""

from dataclasses import dataclass
import re


HARDWARE_SCOPE = (
    "하드웨어 수량·배치 조건만 검사한 결과입니다. 할당·예약 정보는 검사하지 않으며, "
    "실제 즉시 실행 가능 여부나 배정 확정을 의미하지 않습니다."
)


class MatchingError(ValueError):
    """A request/data error, distinct from hardware insufficiency."""

    code = "MATCHING_ERROR"


class InvalidJobID(MatchingError):
    code = "INVALID_JOB_ID"


class JobNotFound(MatchingError):
    code = "JOB_NOT_FOUND"


class MissingJobRequirement(MatchingError):
    code = "MISSING_JOB_REQUIREMENT"


def parse_job_id(value):
    """Accept an unsigned BIGINT ID (including CLI strings such as '001')."""
    if isinstance(value, str) and re.fullmatch(r"[0-9]{1,20}", value):
        value = int(value)
    if type(value) is not int or not 1 <= value <= 18446744073709551615:
        raise InvalidJobID("Job ID는 1 이상 BIGINT UNSIGNED 범위의 정수여야 합니다.")
    return value


@dataclass(frozen=True)
class JobRequirement:
    job_request_id: int
    gpu_model_id: int
    manufacturer: str
    model_name: str
    gpu_count: int
    same_server_required: bool


@dataclass(frozen=True)
class GPU:
    gpu_id: int
    asset_tag: str
    gpu_model_id: int
    hardware_status: str
    server_id: int
    server_name: str
    operational_status: str


@dataclass(frozen=True)
class ServerCapacity:
    server_id: int
    server_name: str
    healthy_gpu_count: int


@dataclass(frozen=True)
class HardwareFeasibility:
    requirement: JobRequirement
    status: str
    reason_codes: tuple[str, ...]
    explanation: str
    eligible_gpu_count: int
    max_gpus_per_server: int
    server_capacities: tuple[ServerCapacity, ...]
    matching_server_ids: tuple[int, ...]
    model_gpus: tuple[GPU, ...]
    candidate_gpus: tuple[GPU, ...]
    scope: str = HARDWARE_SCOPE
