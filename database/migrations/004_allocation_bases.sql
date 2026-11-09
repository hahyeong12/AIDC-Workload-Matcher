-- v0.3-2: additive evidence only; no ALTER/backfill of historical allocations.
-- Apply through database.basis_migrate to the dedicated test database only.
CREATE TABLE allocation_bases (
    allocation_id BIGINT UNSIGNED NOT NULL,
    basis_type VARCHAR(24) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    job_request_id BIGINT UNSIGNED NOT NULL,
    customer_id BIGINT UNSIGNED NOT NULL,
    proposal_id BIGINT UNSIGNED DEFAULT NULL,
    response_id BIGINT UNSIGNED DEFAULT NULL,
    gpu_model_id BIGINT UNSIGNED NOT NULL,
    gpu_count INT NOT NULL,
    same_server_required TINYINT NOT NULL,
    PRIMARY KEY (allocation_id),
    INDEX idx_allocation_bases_job (job_request_id, allocation_id),
    INDEX idx_allocation_bases_customer (customer_id),
    INDEX idx_allocation_bases_proposal (proposal_id, job_request_id, customer_id),
    INDEX idx_allocation_bases_response (response_id),
    INDEX idx_allocation_bases_model (gpu_model_id),
    CONSTRAINT ck_allocation_bases_type CHECK (basis_type IN ('ORIGINAL', 'ACCEPTED_PROPOSAL')),
    CONSTRAINT ck_allocation_bases_source CHECK (
        ((basis_type = 'ORIGINAL') AND (proposal_id IS NULL) AND (response_id IS NULL)) OR
        ((basis_type = 'ACCEPTED_PROPOSAL') AND (proposal_id IS NOT NULL) AND (response_id IS NOT NULL))),
    CONSTRAINT ck_allocation_bases_count CHECK (gpu_count > 0),
    CONSTRAINT ck_allocation_bases_same CHECK (same_server_required IN (0, 1)),
    CONSTRAINT fk_allocation_bases_allocation FOREIGN KEY (allocation_id)
        REFERENCES allocations (allocation_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT fk_allocation_bases_job FOREIGN KEY (job_request_id)
        REFERENCES job_requests (job_request_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT fk_allocation_bases_customer FOREIGN KEY (customer_id)
        REFERENCES customers (customer_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT fk_allocation_bases_proposal FOREIGN KEY (proposal_id, job_request_id, customer_id)
        REFERENCES job_proposals (proposal_id, job_request_id, customer_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT fk_allocation_bases_response FOREIGN KEY (response_id)
        REFERENCES proposal_responses (response_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT fk_allocation_bases_model FOREIGN KEY (gpu_model_id)
        REFERENCES gpu_models (gpu_model_id) ON UPDATE RESTRICT ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
