-- v0.3-1 additive tables only. Use database.proposal_migrate for inspection/apply.
-- Existing 8 tables, original triggers and data are never changed.
-- DDL commits implicitly. Stop on failure; never drop/recreate objects.

CREATE TABLE job_proposals (
    proposal_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    job_request_id BIGINT UNSIGNED NOT NULL,
    customer_id BIGINT UNSIGNED NOT NULL,
    gpu_model_id BIGINT UNSIGNED NOT NULL,
    gpu_count INT NOT NULL,
    same_server_required TINYINT NOT NULL,
    proposal_type VARCHAR(24) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    reason TEXT NOT NULL,
    changes JSON NOT NULL,
    cautions TEXT NOT NULL,
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    PRIMARY KEY (proposal_id),
    CONSTRAINT uq_job_proposals_conditions UNIQUE (job_request_id, gpu_model_id, gpu_count, same_server_required),
    CONSTRAINT uq_job_proposals_identity UNIQUE (proposal_id, job_request_id, customer_id),
    CONSTRAINT ck_job_proposals_count CHECK (gpu_count > 0),
    CONSTRAINT ck_job_proposals_same_server CHECK (same_server_required IN (0, 1)),
    CONSTRAINT ck_job_proposals_type CHECK (proposal_type IN ('DISTRIBUTED', 'REDUCED_QUANTITY')),
    CONSTRAINT ck_job_proposals_distributed CHECK ((proposal_type <> 'DISTRIBUTED') OR (same_server_required = 0)),
    CONSTRAINT ck_job_proposals_reason CHECK (CHAR_LENGTH(TRIM(reason)) > 0),
    CONSTRAINT ck_job_proposals_changes CHECK (JSON_TYPE(changes) = 'ARRAY'),
    CONSTRAINT ck_job_proposals_cautions CHECK (CHAR_LENGTH(TRIM(cautions)) > 0),
    INDEX idx_job_proposals_customer (customer_id),
    INDEX idx_job_proposals_model (gpu_model_id),
    CONSTRAINT fk_job_proposals_job FOREIGN KEY (job_request_id)
        REFERENCES job_requests (job_request_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT fk_job_proposals_customer FOREIGN KEY (customer_id)
        REFERENCES customers (customer_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT fk_job_proposals_model FOREIGN KEY (gpu_model_id)
        REFERENCES gpu_models (gpu_model_id) ON UPDATE RESTRICT ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE proposal_responses (
    response_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    proposal_id BIGINT UNSIGNED NOT NULL,
    job_request_id BIGINT UNSIGNED NOT NULL,
    customer_id BIGINT UNSIGNED NOT NULL,
    decision VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    responded_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    accepted_job_request_id BIGINT UNSIGNED GENERATED ALWAYS AS
        (CASE decision WHEN 'ACCEPTED' THEN job_request_id ELSE NULL END) STORED,
    PRIMARY KEY (response_id),
    CONSTRAINT uq_proposal_responses_proposal UNIQUE (proposal_id, job_request_id, customer_id),
    CONSTRAINT uq_proposal_responses_accepted_job UNIQUE (accepted_job_request_id),
    CONSTRAINT ck_proposal_responses_decision CHECK (decision IN ('ACCEPTED', 'REJECTED')),
    INDEX idx_proposal_responses_job (job_request_id, response_id),
    INDEX idx_proposal_responses_customer (customer_id),
    CONSTRAINT fk_proposal_responses_proposal FOREIGN KEY (proposal_id, job_request_id, customer_id)
        REFERENCES job_proposals (proposal_id, job_request_id, customer_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT fk_proposal_responses_customer FOREIGN KEY (customer_id)
        REFERENCES customers (customer_id) ON UPDATE RESTRICT ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
