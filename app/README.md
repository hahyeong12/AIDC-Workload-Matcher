# 애플리케이션 v0.2-1

`db.py`의 기존 환경변수 기반 연결을 사용해 원본 GPU 요구조건의 하드웨어 수량·배치를 검사합니다.
스키마 변경, 실제 GPU 배정·예약, 대안 생성·동의, 시간 예측, Flask UI와 학습 실행은 포함하지 않습니다.

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
