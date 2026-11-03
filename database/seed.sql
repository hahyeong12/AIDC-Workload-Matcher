-- Entirely fictional test data. Addresses use the reserved .invalid domain.
-- Apply once to empty v0.1 tables, in one transaction managed by the caller.
-- Plain INSERT deliberately fails on duplicates; it never updates existing rows.

INSERT INTO customers (customer_id, display_name, email) VALUES
    (1, '가상 연구팀 알파', 'alpha@example.invalid'),
    (2, '가상 연구팀 베타', 'beta@example.invalid'),
    (3, '가상 연구팀 감마', 'gamma@example.invalid');

INSERT INTO gpu_models (gpu_model_id, manufacturer, model_name, memory_gb) VALUES
    (1, 'NVIDIA', 'H100-SXM-80GB', 80),
    (2, 'NVIDIA', 'A100-SXM-80GB', 80);

INSERT INTO servers (server_id, server_name, operational_status) VALUES
    (1, 'demo-h100-01', 'ONLINE'),
    (2, 'demo-h100-02', 'ONLINE'),
    (3, 'demo-a100-01', 'ONLINE'),
    (4, 'demo-a100-02', 'MAINTENANCE');

INSERT INTO gpus (gpu_id, asset_tag, gpu_model_id, server_id, hardware_status) VALUES
    (1, 'DEMO-H100-001', 1, 1, 'HEALTHY'),
    (2, 'DEMO-H100-002', 1, 1, 'HEALTHY'),
    (3, 'DEMO-H100-003', 1, 1, 'HEALTHY'),
    (4, 'DEMO-H100-004', 1, 1, 'FAULTY'),
    (5, 'DEMO-H100-005', 1, 2, 'HEALTHY'),
    (6, 'DEMO-H100-006', 1, 2, 'HEALTHY'),
    (7, 'DEMO-A100-001', 2, 3, 'HEALTHY'),
    (8, 'DEMO-A100-002', 2, 3, 'HEALTHY'),
    (9, 'DEMO-A100-003', 2, 3, 'MAINTENANCE'),
    (10, 'DEMO-A100-004', 2, 4, 'HEALTHY');

INSERT INTO job_requests (job_request_id, customer_id, title, original_request) VALUES
    (1, 1, '가상 H100 단일 서버 학습', 'H100-SXM-80GB 2개를 반드시 동일 서버에서 사용하고 싶습니다.'),
    (2, 1, '가상 H100 분산 학습', 'H100-SXM-80GB 4개가 필요하며 여러 서버에 배치해도 됩니다.'),
    (3, 2, '가상 H100 대규모 단일 서버 학습', 'H100-SXM-80GB 4개를 반드시 동일 서버에 배치해 주세요.'),
    (4, 2, '가상 A100 소규모 학습', 'A100-SXM-80GB 1개가 필요합니다.'),
    (5, 3, '가상 A100 부족 자원 요청', 'A100-SXM-80GB 3개가 필요하며 동일 서버일 필요는 없습니다.');

INSERT INTO job_requirements (job_request_id, gpu_model_id, gpu_count, same_server_required) VALUES
    (1, 1, 2, 1),
    (2, 1, 4, 0),
    (3, 1, 4, 1),
    (4, 2, 1, 0),
    (5, 2, 3, 0);
