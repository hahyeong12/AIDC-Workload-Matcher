"""Same-model alternatives and terminal consent, without reserving/assigning GPUs."""

from dataclasses import replace

from app.matching import parse_job_id
from app.proposal import (
    AcceptedProposal, ConditionChange, CustomerMismatch, ProposalConflict, ProposalDetail,
    ProposalDraft, ProposalGeneration, ProposalIntegrityError,
)
from app.repositories.proposals import ProposalRepository
from app.services.allocations import allocation_transaction, evaluate_availability


def condition_changes(original, proposed):
    return tuple(ConditionChange(field, getattr(original, field), getattr(proposed, field))
                 for field in ("gpu_model_id", "gpu_count", "same_server_required")
                 if getattr(original, field) != getattr(proposed, field))


def validate_proposal(record, original):
    same_model = record.gpu_model_id == original.gpu_model_id
    if record.proposal_type == "DISTRIBUTED":
        valid = (original.same_server_required and not record.same_server_required
                 and record.gpu_count == original.gpu_count)
    elif record.proposal_type == "REDUCED_QUANTITY":
        valid = (0 < record.gpu_count < original.gpu_count
                 and record.same_server_required == original.same_server_required)
    else:
        valid = False
    if (not same_model or not valid or condition_changes(original, record) != record.changes
            or not record.reason.strip() or not record.cautions.strip()):
        raise ProposalIntegrityError("대안 조건·변경 기록이 원본과 일치하지 않습니다. 기존 기록을 보존하고 점검하세요.")


def generate_drafts(availability):
    """Pure, deterministic: full-count distribution and maximal reduced count."""
    if availability.status == "AVAILABLE":
        return ()
    original = availability.requirement
    choices = []
    if original.same_server_required and availability.available_gpu_count >= original.gpu_count:
        choices.append((original.gpu_count, False, "DISTRIBUTED",
                        "한 서버에서는 부족하지만 여러 ONLINE 서버의 HEALTHY 미할당 GPU 합계로 원본 수량을 확보할 수 있습니다.",
                        "동일 서버 필수 조건을 완화합니다. 실제 분산 학습 지원 및 네트워크 조건은 확인되지 않았으며 별도 검증과 고객 동의가 필요합니다."))
    capacity = availability.max_gpus_per_server if original.same_server_required else availability.available_gpu_count
    reduced = min(original.gpu_count - 1, capacity)
    if reduced > 0:
        choices.append((reduced, original.same_server_required, "REDUCED_QUANTITY",
                        "원본 배치 조건에서 요청 수량을 확보할 수 없어 해당 배치 조건의 최대 가용 수량으로 줄입니다.",
                        "수량 축소가 성능·실행시간·학습 품질에 미치는 영향은 확인되지 않았습니다. 원본과 동일한 결과를 보장하지 않으며 고객 동의가 필요합니다."))
    result = []
    for count, same, kind, reason, cautions in choices:
        proposed = replace(original, gpu_count=count, same_server_required=same)
        # Reuse the same availability evaluator to prove each suggested condition.
        checked = evaluate_availability(proposed, availability.available_gpus, ())
        if checked.status != "AVAILABLE":
            raise ProposalIntegrityError("생성한 대안이 현재 자원 조건을 충족하지 않습니다.")
        draft = ProposalDraft(original.gpu_model_id, count, same, kind, reason,
                              condition_changes(original, proposed), cautions)
        validate_proposal(draft, original)
        result.append(draft)
    return tuple(result)


class ProposalService:
    def __init__(self, connection):
        self.connection = connection
        self.repository = ProposalRepository(connection)

    def locked_job(self, job_id):
        initial = self.repository.get_requirement(job_id)
        # Same global prefix as AllocationService: model -> Job -> requirement.
        self.repository.lock_request(initial)
        requirement = self.repository.get_requirement(job_id)
        if requirement != initial:
            raise ProposalConflict("원본 요구조건이 변경되어 중단했습니다.")
        return requirement, self.repository.owner(job_id)

    def require_no_allocation_history(self, job_id, action):
        """One execution per Job: any ACTIVE/COMPLETED/FAILED allocation settles it.

        Called only while the model -> Job locks are held. Every allocation writer
        (AllocationService.allocate/allocate_proposal) takes the same locks first,
        so an allocation committed while we waited is visible to this READ
        COMMITTED read, and none can be inserted for this Job until this
        transaction ends. The read takes no allocation lock, so the documented
        global lock order is unchanged.
        """
        if self.repository.has_allocation_history(job_id):
            raise ProposalConflict(
                f"이 Job에는 배정 이력이 있어 {action}할 수 없습니다. 원본 Job은 한 번만 실행되며, "
                "다른 조건이 필요하면 새 Job을 등록하세요. 기존 대안의 거절 기록은 남길 수 있습니다.")

    def generate(self, job_id):
        job_id = parse_job_id(job_id)
        with allocation_transaction(self.connection):
            requirement, owner = self.locked_job(job_id)
            inventory = self.repository.lock_inventory(requirement.gpu_model_id)
            self.require_no_allocation_history(job_id, "새 대안을 생성")
            availability = evaluate_availability(
                requirement, inventory, self.repository.occupied_gpu_ids(requirement.gpu_model_id))
            drafts = generate_drafts(availability)
            records = []
            for draft in drafts:
                existing = self.repository.find_conditions(job_id, draft)
                identifier = existing if existing is not None else self.repository.create_proposal(job_id, owner, draft)
                record = self.repository.get_proposal(identifier, lock=True, replayed=existing is not None)
                validate_proposal(record, requirement)
                if record.job_request_id != job_id or record.customer_id != owner:
                    raise ProposalIntegrityError("대안의 원본 Job·고객 관계가 일치하지 않습니다.")
                records.append(record)
            status = ("ORIGINAL_AVAILABLE" if availability.status == "AVAILABLE" else
                      "PROPOSALS_AVAILABLE" if records else "NO_FEASIBLE_ALTERNATIVES")
            reasons = () if records or status == "ORIGINAL_AVAILABLE" else availability.reason_codes + ("NO_FREE_GPUS",)
            result = ProposalGeneration(job_id, availability, status, tuple(records), reasons)
        return result

    def respond(self, job_id, proposal_id, customer_id, decision):
        job_id, proposal_id, customer_id = map(parse_job_id, (job_id, proposal_id, customer_id))
        if decision not in ("ACCEPTED", "REJECTED"):
            raise ProposalConflict("응답은 ACCEPTED 또는 REJECTED여야 합니다.")
        with allocation_transaction(self.connection):
            requirement, owner = self.locked_job(job_id)
            if customer_id != owner:
                raise CustomerMismatch("응답 고객이 원본 Job의 고객과 다릅니다. 이 검사는 실제 인증이 아닙니다.")
            proposal = self.repository.get_proposal(proposal_id, lock=True)
            if proposal.job_request_id != job_id:
                raise ProposalConflict("대안이 요청한 Job에 속하지 않습니다.")
            if proposal.customer_id != owner:
                raise ProposalIntegrityError("대안의 고객 스냅샷이 원본 Job과 다릅니다.")
            validate_proposal(proposal, requirement)
            existing = self.repository.response_for(proposal_id, lock=True)
            if existing is not None:
                if existing.customer_id != owner or existing.job_request_id != job_id:
                    raise ProposalIntegrityError("기존 응답의 고객·Job 관계가 일치하지 않습니다.")
                if existing.decision != decision:
                    raise ProposalConflict("이미 다른 응답으로 확정된 대안입니다. 기존 이력은 변경하지 않습니다.")
                result = replace(existing, replayed=True)
            else:
                if decision == "ACCEPTED" and self.repository.accepted_for_job(job_id, lock=True) is not None:
                    raise ProposalConflict("이 Job에는 이미 수락된 다른 대안이 있습니다.")
                # Only NEW acceptances are refused; the replay branch above and
                # REJECTED responses stay available after execution.
                if decision == "ACCEPTED":
                    self.require_no_allocation_history(job_id, "새로 수락")
                # Acceptance records intent only: deliberately no availability check
                # or AllocationService call. Stale proposals remain historical facts.
                result = self.repository.create_response(proposal, customer_id, decision)
        return result

    def detail(self, proposal_id):
        proposal = self.repository.get_proposal(parse_job_id(proposal_id))
        requirement = self.repository.get_requirement(proposal.job_request_id)
        validate_proposal(proposal, requirement)
        if proposal.customer_id != self.repository.owner(proposal.job_request_id):
            raise ProposalIntegrityError("대안의 고객 관계가 원본과 다릅니다.")
        return ProposalDetail(proposal, requirement, self.repository.response_for(proposal.proposal_id))

    def list_for_job(self, job_id):
        job_id = parse_job_id(job_id)
        self.repository.get_requirement(job_id)
        return tuple(self.detail(record.proposal_id) for record in self.repository.list_proposals(job_id))

    def history(self, job_id):
        job_id = parse_job_id(job_id)
        self.repository.get_requirement(job_id)
        return self.repository.history(job_id)

    def accepted(self, job_id):
        """Read consent evidence for future v0.3-2; never authorizes authentication."""
        job_id = parse_job_id(job_id)
        self.repository.get_requirement(job_id)
        response = self.repository.accepted_for_job(job_id)
        if response is None:
            return None
        detail = self.detail(response.proposal_id)
        if response.customer_id != detail.proposal.customer_id:
            raise ProposalIntegrityError("수락 고객 관계가 일치하지 않습니다.")
        return AcceptedProposal(detail.proposal, response)
