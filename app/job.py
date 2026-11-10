"""Job registration inputs and immutable query results."""

from dataclasses import dataclass
from datetime import datetime

from app.matching import InvalidJobID, MatchingError, parse_job_id


class InvalidJobInput(MatchingError):
    code = "INVALID_JOB_INPUT"


class CustomerNotFound(MatchingError):
    code = "CUSTOMER_NOT_FOUND"


class GPUModelNotFound(MatchingError):
    code = "GPU_MODEL_NOT_FOUND"


def parse_id(value, field):
    try:
        return parse_job_id(value)
    except InvalidJobID:
        raise InvalidJobInput(f"{field}: 1 이상 BIGINT UNSIGNED 정수 ID가 필요합니다.") from None


def validate_registration(customer_id, title, original_request, gpu_model_id,
                          gpu_count, same_server_required):
    customer_id = parse_id(customer_id, "customer_id")
    gpu_model_id = parse_id(gpu_model_id, "gpu_model_id")
    if not isinstance(title, str) or not title.strip() or len(title) > 200:
        raise InvalidJobInput("title: 공백이 아닌 1~200자 문자열이 필요합니다.")
    if not isinstance(original_request, str) or not original_request.strip():
        raise InvalidJobInput("original_request: 비어 있지 않은 문자열이 필요합니다.")
    try:
        size = len(original_request.encode("utf-8"))
        title.encode("utf-8")
    except UnicodeEncodeError:
        raise InvalidJobInput("title/original_request: 유효한 UTF-8 문자열이 필요합니다.") from None
    if size > 65535:
        raise InvalidJobInput("original_request: UTF-8 65,535바이트를 초과합니다.")
    if type(gpu_count) is not int or not 1 <= gpu_count <= 2147483647:
        raise InvalidJobInput("gpu_count: 1~2147483647 정수가 필요합니다.")
    if type(same_server_required) not in (bool, int) or same_server_required not in (0, 1):
        raise InvalidJobInput("same_server_required: bool 또는 정수 0/1이 필요합니다.")
    return customer_id, title, original_request, gpu_model_id, gpu_count, int(same_server_required)


@dataclass(frozen=True)
class JobDetail:
    job_request_id: int
    customer_id: int
    title: str
    original_request: str
    created_at: datetime
    gpu_model_id: int
    gpu_count: int
    same_server_required: bool
