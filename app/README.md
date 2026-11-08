# 애플리케이션 v0.2-2

`db.py`의 기존 환경변수 기반 연결을 사용해 원본 GPU 요구조건의 하드웨어 수량·배치를 검사합니다.
v0.2-1 하드웨어 매칭은 그대로 유지하고 v0.2-2 배정·반환 CLI를 별도로 제공합니다. 예약, 대안 생성·동의, 시간 예측, Flask UI와 실제 학습 실행은 포함하지 않습니다.

```powershell
# 개발 DB: 읽기 전용 검사
.\.venv\Scripts\python.exe -m app.match_cli 001
# 전용 테스트 DB: TEST_MYSQL_*만 사용
.\.venv\Scripts\python.exe -m app.match_cli 003 --test
```

Job ID는 1부터 BIGINT UNSIGNED 최댓값까지의 정수이며 `001`도 허용합니다.
`.env`와 프로세스 환경변수를 사용하고 접속 값은 출력하지 않습니다. 준비 방법은 [DB 안내](../database/README.md)를 따릅니다.

| 파일 | 책임 |
| --- | --- |
| `matching.py` | 불변 입력·출력 데이터, Job ID 검증, 별도 입력·데이터 오류 |
| `repositories/hardware.py` | 원본 요구조건과 요청 모델의 개별 GPU·서버 조회, 매개변수화 SELECT |
| `services/hardware.py` | ONLINE + HEALTHY 후보 필터, 서버별 집계, 하드웨어 판단·설명 |
| `match_cli.py` | 별도 읽기 전용 스냅샷 트랜잭션, JSON 출력과 오류·종료 코드 |

동일 서버 필수이면 한 서버에서 전 수량을 확보할 수 있어야 합니다. 분산 허용이면 한 서버 또는 여러 서버의 수량을 합산합니다.
모델은 원본 모델 PK로 일치시킵니다. 제외된 장비도 `model_gpus`에서 상태를 확인할 수 있습니다.

| 주요 JSON 필드 | 의미 |
| --- | --- |
| `requirement` | 원본 Job ID, 모델 ID·제조사·모델명, 수량, 동일 서버 플래그 |
| `status` | HARDWARE_FEASIBLE / HARDWARE_INSUFFICIENT |
| `reason_codes` | MODEL_UNAVAILABLE, QUANTITY_SHORTAGE, SAME_SERVER_SHORTAGE 중 해당 사유들 |
| `explanation` | 모델·필요 수량·후보 수량·동일 서버 조건의 충족/부족 설명 |
| `eligible_gpu_count`, `max_gpus_per_server` | 전체 후보 수와 한 서버의 최대 후보 수 |
| `server_capacities` | 후보가 있는 서버별 정상 GPU 수량 |
| `matching_server_ids` | 해당 요청 전 수량을 한 서버에서 충족할 수 있는 서버 ID 목록 |
| `model_gpus`, `candidate_gpus` | 요청 모델 전체 장비와 ONLINE + HEALTHY 후보 장비 |
| `scope` | 하드웨어 검사 범위와 할당·예약 미검사 안내 |

후보 목록은 선택·배정 결과가 아닙니다. 하드웨어 조건 충족은 실제 즉시 실행 가능 여부나 배정 확정을 의미하지 않습니다.
분산 허용 요청은 `matching_server_ids`가 비어 있어도 전체 후보 수량으로 충족할 수 있습니다.

종료 코드 0은 판단 완료이며 하드웨어 부족도 포함합니다. 코드 2는 INVALID_JOB_ID / JOB_NOT_FOUND / MISSING_JOB_REQUIREMENT이며 표준 오류에 JSON을 출력합니다.
argparse 사용법 오류도 코드 2로 종료합니다. 코드 1은 DB 설정·접속·조회 오류이며 비밀정보를 제외한 진단을 제공합니다.

CLI는 모든 조회를 같은 읽기 전용 스냅샷에서 실행하고 끝나면 ROLLBACK 후 연결을 닫습니다.
서비스를 직접 사용하는 경우 `check_hardware(HardwareRepository(connection), job_id)`를 호출하며 트랜잭션 일관성과 종료는 호출자가 관리합니다.
계층과 향후 배정 시 재검증 방향은 [아키텍처](../docs/architecture.md)를 따릅니다.

## v0.2-2 배정 CLI

개발 DB에는 아직 추가 테이블이 없습니다. 현재 배정 명령은 마이그레이션이 완료된 테스트 DB에 `--test`로 실행합니다. 다음은 명령 형식이며 `<ID>`는 실제 숫자로 바꿉니다.

```powershell
.\.venv\Scripts\python.exe -m app.allocation_cli --test available <job_id>
.\.venv\Scripts\python.exe -m app.allocation_cli --test allocate <job_id>
.\.venv\Scripts\python.exe -m app.allocation_cli --test complete <allocation_id>
.\.venv\Scripts\python.exe -m app.allocation_cli --test fail <allocation_id>
.\.venv\Scripts\python.exe -m app.allocation_cli --test history <gpu_id>
```

| 파일 | 책임 |
| --- | --- |
| `allocation.py` | 가용성·배정·개별 GPU 불변 결과와 배정 오류 |
| `repositories/allocations.py` | 매개변수화 SELECT/INSERT/UPDATE, FOR UPDATE, GPU별 이력 |
| `services/allocations.py` | 트랜잭션 경계, 잠금 후 재검증, GPU 선택·반환·멱등성 |
| `allocation_cli.py` | 명령별 ID와 DB 선택, JSON 및 종료 코드 |

`available`과 `history`는 읽기 전용 REPEATABLE READ 스냅샷을 종료 시 롤백합니다. `allocate`·`complete`·`fail`은 전용 연결의 READ COMMITTED 트랜잭션을 실제 커밋합니다. 진행 중인 트랜잭션을 가진 연결은 서비스가 거부합니다.

| 주요 JSON 필드 | 의미 |
| --- | --- |
| 가용 조회 `status` | AVAILABLE / INSUFFICIENT_RESOURCES; 배정 확정 전 참고 정보 |
| `available_gpu_count`, `max_gpus_per_server` | ONLINE + HEALTHY이고 점유되지 않은 GPU 수, 서버별 최대 수 |
| `occupied_gpu_ids`, `available_gpus` | 기존 점유 GPU ID와 현재 미점유 후보 |
| `allocation_id`, `job_request_id`, `status` | 배정 ID, 원본 Job, ACTIVE / COMPLETED / FAILED |
| `allocated_at`, `ended_at`, `gpus` | UTC 배정·종료 시각, GPU·배정 당시 서버·배정/해제 시각 |
| `replayed` | 반복 요청으로 기존 결과를 반환했는지 여부 |
| 이력 `gpu_id`, `history` | GPU별 모든 할당·해제 기록 |

가용 조회는 미해제 자식 또는 ACTIVE 헤더가 있는 GPU를 제외합니다. 실제 배정은 모델 → Job → 요구조건 → 서버 → GPU → 할당 → 할당-GPU 순으로 잠그고 재검증합니다. 동일 서버 필수이면 한 서버에서만 선택하며, 부족하면 부분 배정을 남기지 않습니다.
완료·실패는 전 GPU 반환과 헤더 상태 전이를 함께 커밋합니다. FAILED는 배정 확정 후 실행 실패입니다. 배정 전 부족·SQL 오류는 롤백하며 FAILED 행을 만들지 않습니다.

같은 Job의 반복 배정은 종료 후에도 기존 결과를 반환합니다. 같은 종료 상태의 반복 호출은 시각을 보존하고, 다른 종료 상태로 바꾸려 하면 충돌입니다. 새 실행을 만들려면 별도 재실행 정책이 필요합니다.
서비스는 이력을 삭제하지 않습니다. CLI 쓰기 예시를 수동 실행하면 테스트 DB에도 기록이 남으며, seed Job에 기존 이력이 있으면 통합 스위트는 그 이력을 보존하기 위해 중단합니다. 안전한 검증은 [pytest 실행 안내](../tests/README.md)를 따릅니다.

종료 코드 0은 처리 성공이며 `available`의 부족 판단도 포함합니다. 코드 2는 입력·대상·부족·충돌·무결성 오류(JSON), 코드 1은 DB 설정·접속·SQL 오류입니다. 실제 실행 결과는 [v0.2-2 검증 기록](../docs/v0.2-2-validation.md)에 있습니다.
