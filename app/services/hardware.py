"""Evaluate physical hardware only; never reserve or assign a GPU."""

from collections import defaultdict

from app.matching import HardwareFeasibility, ServerCapacity, parse_job_id


def evaluate_hardware(requirement, inventory):
    """Pure decision over one inventory snapshot; model identity is exact by PK."""
    model_gpus = tuple(sorted(
        (gpu for gpu in inventory if gpu.gpu_model_id == requirement.gpu_model_id),
        key=lambda gpu: (gpu.server_id, gpu.gpu_id)))
    candidates = tuple(gpu for gpu in model_gpus
                       if gpu.hardware_status == "HEALTHY" and gpu.operational_status == "ONLINE")
    grouped = defaultdict(list)
    for gpu in candidates:
        grouped[gpu.server_id].append(gpu)
    capacities = tuple(ServerCapacity(server_id, gpus[0].server_name, len(gpus))
                       for server_id, gpus in sorted(grouped.items()))
    total = len(candidates)
    maximum = max((server.healthy_gpu_count for server in capacities), default=0)
    matching_servers = tuple(server.server_id for server in capacities
                             if server.healthy_gpu_count >= requirement.gpu_count)
    reasons, explanations = [], []
    model = f"{requirement.manufacturer} {requirement.model_name}"
    if not model_gpus:
        reasons.append("MODEL_UNAVAILABLE")
        explanations.append(f"요청 모델 {model}의 개별 GPU 장비가 없습니다.")
    if total < requirement.gpu_count:
        reasons.append("QUANTITY_SHORTAGE")
        explanations.append(
            f"요청 모델 {model}은 {requirement.gpu_count}개가 필요하지만 "
            f"ONLINE 서버의 HEALTHY GPU는 총 {total}개로 {requirement.gpu_count - total}개 부족합니다.")
    if requirement.same_server_required and maximum < requirement.gpu_count:
        reasons.append("SAME_SERVER_SHORTAGE")
        explanations.append(
            f"동일 서버에 {requirement.gpu_count}개가 필요하지만 서버 한 대의 최대 후보 수는 {maximum}개입니다. "
            f"여러 서버의 총 {total}개를 합산해 동일 서버 조건을 충족할 수 없습니다.")
    if reasons:
        excluded = len(model_gpus) - total
        if excluded:
            explanations.append(
                f"요청 모델 장비 중 {excluded}개는 서버 상태가 ONLINE이 아니거나 "
                "GPU 상태가 HEALTHY가 아니므로 제외했습니다.")
    elif requirement.same_server_required:
        explanations.append(
            f"요청 모델 {model} {requirement.gpu_count}개를 동일 서버에서 확보할 하드웨어 구성이 "
            f"{len(matching_servers)}개 서버에 존재합니다.")
    else:
        explanations.append(
            f"분산 배치가 허용되며 요청 모델 {model}은 {requirement.gpu_count}개가 필요합니다. "
            f"{len(capacities)}개 ONLINE 서버의 HEALTHY GPU 총 {total}개로 하드웨어 수량 조건을 충족합니다.")
    return HardwareFeasibility(
        requirement=requirement,
        status="HARDWARE_INSUFFICIENT" if reasons else "HARDWARE_FEASIBLE",
        reason_codes=tuple(reasons), explanation=" ".join(explanations),
        eligible_gpu_count=total, max_gpus_per_server=maximum,
        server_capacities=capacities, matching_server_ids=matching_servers,
        model_gpus=model_gpus, candidate_gpus=candidates)


def check_hardware(repository, job_id):
    """Load original conditions and inventory on the caller's transaction."""
    job_id = parse_job_id(job_id)
    requirement = repository.get_requirement(job_id)
    return evaluate_hardware(requirement, repository.list_model_gpus(requirement.gpu_model_id))
