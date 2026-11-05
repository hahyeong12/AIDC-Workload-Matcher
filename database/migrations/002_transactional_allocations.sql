-- v0.2-2 additive migration. Use database.migrate for preflight/resume.
-- Never rerun schema.sql against an existing database.
-- DDL commits implicitly; run in a maintenance window, stop on any error.

CREATE TABLE allocations (
    allocation_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    job_request_id BIGINT UNSIGNED NOT NULL,
    status VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL DEFAULT 'ACTIVE',
    allocated_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    ended_at TIMESTAMP(6) NULL DEFAULT NULL,
    active_job_request_id BIGINT UNSIGNED GENERATED ALWAYS AS
        (CASE status WHEN 'ACTIVE' THEN job_request_id ELSE NULL END) STORED,
    PRIMARY KEY (allocation_id),
    CONSTRAINT uq_allocations_active_job UNIQUE (active_job_request_id),
    INDEX idx_allocations_job (job_request_id, allocation_id),
    CONSTRAINT ck_allocations_status CHECK (status IN ('ACTIVE', 'COMPLETED', 'FAILED')),
    CONSTRAINT ck_allocations_end_state CHECK ((status = 'ACTIVE') = (ended_at IS NULL)),
    CONSTRAINT ck_allocations_end_time CHECK ((ended_at IS NULL) OR (ended_at >= allocated_at)),
    CONSTRAINT fk_allocations_job FOREIGN KEY (job_request_id)
        REFERENCES job_requests (job_request_id) ON UPDATE RESTRICT ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE allocation_gpus (
    allocation_id BIGINT UNSIGNED NOT NULL,
    gpu_id BIGINT UNSIGNED NOT NULL,
    server_id BIGINT UNSIGNED NOT NULL,
    assigned_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    released_at TIMESTAMP(6) NULL DEFAULT NULL,
    active_gpu_id BIGINT UNSIGNED GENERATED ALWAYS AS
        (IF((released_at IS NULL), gpu_id, NULL)) STORED,
    PRIMARY KEY (allocation_id, gpu_id),
    CONSTRAINT uq_allocation_gpus_active_gpu UNIQUE (active_gpu_id),
    INDEX idx_allocation_gpus_history (gpu_id, allocation_id),
    INDEX idx_allocation_gpus_server (server_id),
    CONSTRAINT ck_allocation_gpus_release CHECK ((released_at IS NULL) OR (released_at >= assigned_at)),
    CONSTRAINT fk_allocation_gpus_allocation FOREIGN KEY (allocation_id)
        REFERENCES allocations (allocation_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT fk_allocation_gpus_gpu FOREIGN KEY (gpu_id)
        REFERENCES gpus (gpu_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT fk_allocation_gpus_server FOREIGN KEY (server_id)
        REFERENCES servers (server_id) ON UPDATE RESTRICT ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
