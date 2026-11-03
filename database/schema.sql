-- AIDC-Workload-Matcher v0.1 / MySQL 8.0.16+ / InnoDB.
-- Run only in an explicitly selected, empty project database.
-- No database creation, replacement, deletion, or IF NOT EXISTS masking.

CREATE TABLE customers (
    customer_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    display_name VARCHAR(100) NOT NULL,
    email VARCHAR(254) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (customer_id),
    CONSTRAINT uq_customers_email UNIQUE (email),
    CONSTRAINT ck_customers_name CHECK (CHAR_LENGTH(TRIM(display_name)) > 0),
    CONSTRAINT ck_customers_email CHECK (CHAR_LENGTH(TRIM(email)) > 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE gpu_models (
    gpu_model_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    manufacturer VARCHAR(50) NOT NULL,
    model_name VARCHAR(100) NOT NULL,
    memory_gb SMALLINT UNSIGNED NOT NULL,
    PRIMARY KEY (gpu_model_id),
    CONSTRAINT uq_gpu_models_identity UNIQUE (manufacturer, model_name),
    CONSTRAINT ck_gpu_models_manufacturer CHECK (CHAR_LENGTH(TRIM(manufacturer)) > 0),
    CONSTRAINT ck_gpu_models_name CHECK (CHAR_LENGTH(TRIM(model_name)) > 0),
    CONSTRAINT ck_gpu_models_memory CHECK (memory_gb > 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE servers (
    server_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    server_name VARCHAR(100) NOT NULL,
    operational_status VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL DEFAULT 'ONLINE',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (server_id),
    CONSTRAINT uq_servers_name UNIQUE (server_name),
    CONSTRAINT ck_servers_name CHECK (CHAR_LENGTH(TRIM(server_name)) > 0),
    CONSTRAINT ck_servers_status CHECK (operational_status IN ('ONLINE', 'MAINTENANCE', 'OFFLINE'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE gpus (
    gpu_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    asset_tag VARCHAR(64) NOT NULL,
    gpu_model_id BIGINT UNSIGNED NOT NULL,
    server_id BIGINT UNSIGNED NOT NULL,
    hardware_status VARCHAR(16) CHARACTER SET ascii COLLATE ascii_bin NOT NULL DEFAULT 'HEALTHY',
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (gpu_id),
    CONSTRAINT uq_gpus_asset_tag UNIQUE (asset_tag),
    CONSTRAINT ck_gpus_asset_tag CHECK (CHAR_LENGTH(TRIM(asset_tag)) > 0),
    CONSTRAINT ck_gpus_status CHECK (hardware_status IN ('HEALTHY', 'FAULTY', 'MAINTENANCE')),
    INDEX idx_gpus_model_status (gpu_model_id, hardware_status),
    INDEX idx_gpus_server_status (server_id, hardware_status),
    CONSTRAINT fk_gpus_model FOREIGN KEY (gpu_model_id)
        REFERENCES gpu_models (gpu_model_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT fk_gpus_server FOREIGN KEY (server_id)
        REFERENCES servers (server_id) ON UPDATE RESTRICT ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE job_requests (
    job_request_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    customer_id BIGINT UNSIGNED NOT NULL,
    title VARCHAR(200) NOT NULL,
    original_request TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (job_request_id),
    CONSTRAINT ck_job_requests_title CHECK (CHAR_LENGTH(TRIM(title)) > 0),
    CONSTRAINT ck_job_requests_original CHECK (CHAR_LENGTH(TRIM(original_request)) > 0),
    INDEX idx_job_requests_customer (customer_id),
    CONSTRAINT fk_job_requests_customer FOREIGN KEY (customer_id)
        REFERENCES customers (customer_id) ON UPDATE RESTRICT ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE job_requirements (
    job_request_id BIGINT UNSIGNED NOT NULL,
    gpu_model_id BIGINT UNSIGNED NOT NULL,
    gpu_count INT NOT NULL,
    same_server_required TINYINT NOT NULL,
    PRIMARY KEY (job_request_id),
    CONSTRAINT ck_job_requirements_count CHECK (gpu_count > 0),
    CONSTRAINT ck_job_requirements_same_server CHECK (same_server_required IN (0, 1)),
    INDEX idx_job_requirements_model (gpu_model_id),
    CONSTRAINT fk_job_requirements_request FOREIGN KEY (job_request_id)
        REFERENCES job_requests (job_request_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT fk_job_requirements_model FOREIGN KEY (gpu_model_id)
        REFERENCES gpu_models (gpu_model_id) ON UPDATE RESTRICT ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- Immutable originals. Execution status and alternatives belong in future tables.
-- Each trigger has a single statement, so DELIMITER changes are unnecessary.
CREATE TRIGGER trg_job_requests_no_update
BEFORE UPDATE ON job_requests FOR EACH ROW
SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Original job requests cannot be updated';

CREATE TRIGGER trg_job_requests_no_delete
BEFORE DELETE ON job_requests FOR EACH ROW
SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Original job requests cannot be deleted';

CREATE TRIGGER trg_job_requirements_no_update
BEFORE UPDATE ON job_requirements FOR EACH ROW
SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Original job requirements cannot be updated';

CREATE TRIGGER trg_job_requirements_no_delete
BEFORE DELETE ON job_requirements FOR EACH ROW
SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'Original job requirements cannot be deleted';
