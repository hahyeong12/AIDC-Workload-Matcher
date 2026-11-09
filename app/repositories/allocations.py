"""Parameterized SQL only; allocation services own commit/rollback boundaries."""

from contextlib import closing

from app.allocation import AllocationBasis, AllocationConflict, AllocationGPU, AllocationNotFound, AllocationResult
from app.db import database_step
from app.matching import GPU
from app.repositories.hardware import HardwareRepository


class AllocationRepository(HardwareRepository):
    def query(self, sql, params=(), *, write=False):
        with database_step("allocation-query", "allocations"):
            with closing(self.connection.cursor()) as cursor:
                cursor.execute(sql, params)
                return (cursor.lastrowid, cursor.rowcount) if write else cursor.fetchall()

    def lock_request(self, requirement):
        # Global order: model -> Job -> requirement -> servers -> GPUs ->
        # allocation headers -> allocation-GPU rows. All IDs ascend per class.
        self.query("SELECT gpu_model_id FROM gpu_models WHERE gpu_model_id = %s FOR UPDATE",
                   (requirement.gpu_model_id,))
        self.query("SELECT job_request_id FROM job_requests WHERE job_request_id = %s FOR UPDATE",
                   (requirement.job_request_id,))
        self.query("SELECT job_request_id FROM job_requirements WHERE job_request_id = %s FOR UPDATE",
                   (requirement.job_request_id,))

    def lock_inventory(self, model_id):
        positions = self.query(
            "SELECT gpu_id, server_id FROM gpus WHERE gpu_model_id = %s ORDER BY gpu_id", (model_id,))
        servers = {}
        for server_id in sorted({row[1] for row in positions}):
            rows = self.query("SELECT server_name, operational_status FROM servers "
                              "WHERE server_id = %s FOR UPDATE", (server_id,))
            if not rows:
                raise AllocationConflict("서버 구성이 변경되었습니다. 같은 Job으로 재시도하세요.")
            servers[server_id] = rows[0]
        inventory = []
        for gpu_id, _ in positions:
            rows = self.query("SELECT gpu_id, asset_tag, gpu_model_id, hardware_status, server_id "
                              "FROM gpus WHERE gpu_id = %s FOR UPDATE", (gpu_id,))
            if not rows or rows[0][2] != model_id or rows[0][4] not in servers:
                raise AllocationConflict("GPU 소속이 변경되었습니다. 같은 Job으로 재시도하세요.")
            row = rows[0]
            name, status = servers[row[4]]
            inventory.append(GPU(*row, name, status))
        return tuple(inventory)

    def occupied_gpu_ids(self, model_id):
        # Unreleased rows are holds, even if an unsupported external writer has
        # changed only the header. ACTIVE headers with released children also
        # remain unavailable until the inconsistency is investigated.
        rows = self.query(
            "SELECT ag.gpu_id FROM allocation_gpus ag "
            "JOIN allocations a ON a.allocation_id = ag.allocation_id "
            "JOIN gpus g ON g.gpu_id = ag.gpu_id "
            "WHERE g.gpu_model_id = %s AND (ag.released_at IS NULL OR a.status = 'ACTIVE') "
            "ORDER BY ag.gpu_id", (model_id,))
        return tuple(sorted({row[0] for row in rows}))

    def latest_for_job(self, job_id):
        rows = self.query("SELECT allocation_id FROM allocations WHERE job_request_id = %s "
                          "ORDER BY allocation_id DESC LIMIT 1 FOR UPDATE", (job_id,))
        return rows[0][0] if rows else None

    def get_allocation(self, allocation_id, *, lock=False, replayed=False):
        suffix = " FOR UPDATE" if lock else ""
        rows = self.query("SELECT allocation_id, job_request_id, status, allocated_at, ended_at "
                          "FROM allocations WHERE allocation_id = %s" + suffix, (allocation_id,))
        if not rows:
            raise AllocationNotFound("해당 할당이 존재하지 않습니다.")
        gpus = self.query("SELECT gpu_id, server_id, assigned_at, released_at FROM allocation_gpus "
                          "WHERE allocation_id = %s ORDER BY gpu_id" + suffix, (allocation_id,))
        return AllocationResult(*rows[0], tuple(AllocationGPU(*row) for row in gpus), replayed,
                                self.get_basis(allocation_id, lock=lock))

    def has_bases(self):
        return bool(self.query("SELECT TABLE_NAME FROM information_schema.TABLES "
                               "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'allocation_bases'"))

    def get_basis(self, allocation_id, *, lock=False):
        if not self.has_bases():
            return None
        rows = self.query("SELECT basis_type, job_request_id, customer_id, proposal_id, response_id, "
                          "gpu_model_id, gpu_count, same_server_required FROM allocation_bases "
                          "WHERE allocation_id = %s" + (" FOR UPDATE" if lock else ""), (allocation_id,))
        return AllocationBasis(*rows[0][:-1], bool(rows[0][-1])) if rows else None

    def create_basis(self, allocation_id, basis):
        self.query("INSERT INTO allocation_bases (allocation_id, basis_type, job_request_id, customer_id, "
                   "proposal_id, response_id, gpu_model_id, gpu_count, same_server_required) "
                   "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                   (allocation_id, basis.basis_type, basis.job_request_id, basis.customer_id,
                    basis.proposal_id, basis.response_id, basis.gpu_model_id, basis.gpu_count,
                    int(basis.same_server_required)), write=True)

    def history_for_job(self, job_id):
        return tuple(self.get_allocation(row[0]) for row in self.query(
            "SELECT allocation_id FROM allocations WHERE job_request_id = %s ORDER BY allocation_id", (job_id,)))

    def create(self, job_id, gpus):
        allocated_at = self.query("SELECT UTC_TIMESTAMP(6)")[0][0]
        allocation_id, _ = self.query(
            "INSERT INTO allocations (job_request_id, status, allocated_at) VALUES (%s, 'ACTIVE', %s)",
            (job_id, allocated_at), write=True)
        for gpu in sorted(gpus, key=lambda item: item.gpu_id):
            self.query("INSERT INTO allocation_gpus (allocation_id, gpu_id, server_id, assigned_at) "
                       "VALUES (%s, %s, %s, %s)",
                       (allocation_id, gpu.gpu_id, gpu.server_id, allocated_at), write=True)
        return allocation_id

    def end(self, allocation_id, status):
        ended_at = self.query("SELECT UTC_TIMESTAMP(6)")[0][0]
        _, released = self.query(
            "UPDATE allocation_gpus SET released_at = %s WHERE allocation_id = %s AND released_at IS NULL",
            (ended_at, allocation_id), write=True)
        _, updated = self.query("UPDATE allocations SET status = %s, ended_at = %s "
                                "WHERE allocation_id = %s AND status = 'ACTIVE'",
                                (status, ended_at, allocation_id), write=True)
        return released, updated

    def history_for_gpu(self, gpu_id):
        return self.query(
            "SELECT a.allocation_id, a.job_request_id, a.status, ag.server_id, "
            "ag.assigned_at, ag.released_at FROM allocation_gpus ag "
            "JOIN allocations a ON a.allocation_id = ag.allocation_id "
            "WHERE ag.gpu_id = %s ORDER BY ag.assigned_at, a.allocation_id", (gpu_id,))
