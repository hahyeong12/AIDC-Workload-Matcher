"""Parameterized append/read SQL only. No UPDATE, DELETE or GPU writes."""

from contextlib import closing
from dataclasses import asdict
import json

from app.db import database_step
from app.proposal import ConditionChange, CustomerResponse, ProposalNotFound, ProposalRecord
from app.repositories.allocations import AllocationRepository


PROPOSAL_COLUMNS = ("proposal_id, job_request_id, customer_id, gpu_model_id, gpu_count, "
                    "same_server_required, proposal_type, reason, changes, cautions, created_at")
RESPONSE_COLUMNS = "response_id, proposal_id, job_request_id, customer_id, decision, responded_at"


class ProposalRepository(AllocationRepository):
    """Reuse v0.2-2 inventory/hold reads and ordered locks, never its write APIs."""

    def query(self, sql, params=(), *, write=False):
        with database_step("proposal-query", "job_proposals"):
            with closing(self.connection.cursor()) as cursor:
                cursor.execute(sql, params)
                return (cursor.lastrowid, cursor.rowcount) if write else cursor.fetchall()

    def owner(self, job_id):
        return self.query("SELECT customer_id FROM job_requests WHERE job_request_id = %s", (job_id,))[0][0]

    @staticmethod
    def record(row, replayed=False):
        values = list(row)
        values[5] = bool(values[5])
        values[8] = tuple(ConditionChange(**change) for change in json.loads(values[8]))
        return ProposalRecord(*values, replayed=replayed)

    def get_proposal(self, proposal_id, *, lock=False, replayed=False):
        rows = self.query(f"SELECT {PROPOSAL_COLUMNS} FROM job_proposals WHERE proposal_id = %s"
                          + (" FOR UPDATE" if lock else ""), (proposal_id,))
        if not rows:
            raise ProposalNotFound("해당 대안이 존재하지 않습니다.")
        return self.record(rows[0], replayed)

    def find_conditions(self, job_id, draft):
        rows = self.query(
            "SELECT proposal_id FROM job_proposals WHERE job_request_id = %s AND gpu_model_id = %s "
            "AND gpu_count = %s AND same_server_required = %s FOR UPDATE",
            (job_id, draft.gpu_model_id, draft.gpu_count, int(draft.same_server_required)))
        return rows[0][0] if rows else None

    def create_proposal(self, job_id, customer_id, draft):
        identifier, _ = self.query(
            "INSERT INTO job_proposals (job_request_id, customer_id, gpu_model_id, gpu_count, "
            "same_server_required, proposal_type, reason, changes, cautions) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (job_id, customer_id, draft.gpu_model_id, draft.gpu_count, int(draft.same_server_required),
             draft.proposal_type, draft.reason, json.dumps([asdict(item) for item in draft.changes]), draft.cautions),
            write=True)
        return identifier

    def list_proposals(self, job_id):
        return tuple(self.record(row) for row in self.query(
            f"SELECT {PROPOSAL_COLUMNS} FROM job_proposals WHERE job_request_id = %s ORDER BY proposal_id", (job_id,)))

    def response_for(self, proposal_id, *, lock=False):
        rows = self.query(f"SELECT {RESPONSE_COLUMNS} FROM proposal_responses WHERE proposal_id = %s"
                          + (" FOR UPDATE" if lock else ""), (proposal_id,))
        return CustomerResponse(*rows[0]) if rows else None

    def accepted_for_job(self, job_id, *, lock=False):
        rows = self.query(f"SELECT {RESPONSE_COLUMNS} FROM proposal_responses WHERE accepted_job_request_id = %s"
                          + (" FOR UPDATE" if lock else ""), (job_id,))
        return CustomerResponse(*rows[0]) if rows else None

    def create_response(self, proposal, customer_id, decision):
        self.query("INSERT INTO proposal_responses (proposal_id, job_request_id, customer_id, decision) "
                   "VALUES (%s, %s, %s, %s)",
                   (proposal.proposal_id, proposal.job_request_id, customer_id, decision), write=True)
        return self.response_for(proposal.proposal_id, lock=True)

    def history(self, job_id):
        return tuple(CustomerResponse(*row) for row in self.query(
            f"SELECT {RESPONSE_COLUMNS} FROM proposal_responses WHERE job_request_id = %s ORDER BY response_id", (job_id,)))
