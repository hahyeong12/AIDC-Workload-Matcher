# AIDC-Workload-Matcher

컴퓨터공학과 데이터베이스 텀프로젝트로 개발하는 AI 데이터센터(AIDC) 작업 매칭 및 자원 배정 시스템입니다.
고객의 AI 학습 작업 요청을 데이터센터의 실제 GPU 자원과 운영 조건에 비교하여 실행 가능성을 판단하고, 가능한 실행 계획과 대안을 제시합니다.

## 프로젝트 목적

- GPU를 개별 장비로 관리하고 모델, 소속 서버, 장비 상태를 기록합니다.
- 장비 상태와 할당 기록으로 사용 가능한 GPU를 계산합니다.
- 최초 작업 요청과 요구조건을 보존하고, 변경된 조건은 별도 대안으로 관리합니다.
- 고객이 대안을 수락하면 자원을 다시 확인하고 트랜잭션으로 배정을 확정합니다.
- 실제 학습과 GPU 연산은 시뮬레이션하고, 관계형 DB 설계와 정확한 자원 배정에 집중합니다.

## 기술 스택

| 구분 | 기술 | 용도 |
| --- | --- | --- |
| 백엔드 | Python, Flask | 요청 처리 및 배정 서비스 |
| 데이터베이스 | MySQL | 자원·요청·대안·할당 기록 관리 |
| 테스트 | pytest | 요구조건 및 배정 무결성 검증 |
| 화면 | HTML/CSS/JavaScript | 자원 조회, 요청 등록, 대안 확인 및 수락 |
| 협업 | GitHub | 소스 버전 관리 및 변경 검토 |

## v0.2-1 현재 구조

```text
AIDC-Workload-Matcher/
├── README.md
├── .gitattributes
├── .gitignore
├── .env.example
├── requirements.txt
├── pytest.ini
├── docs/
│   ├── requirements.md
│   ├── architecture.md
│   ├── decisions.md
│   ├── v0.2-1-validation.md
│   └── mysql-recovery.md
├── database/
│   ├── README.md
│   ├── __init__.py
│   ├── schema.sql
│   ├── seed.sql
│   ├── erd.md
│   ├── initialize.py
│   ├── inspect_schema.py
│   ├── recover.py
│   ├── admin_missing_triggers.sql
│   └── admin_missing_triggers_test.sql
├── app/
│   ├── README.md
│   ├── __init__.py
│   ├── db.py
│   ├── matching.py
│   ├── match_cli.py
│   ├── repositories/
│   │   └── hardware.py
│   └── services/
│       └── hardware.py
└── tests/
    ├── README.md
    ├── conftest.py
    ├── mysql_audit.py
    ├── test_hardware_matching.py
    ├── test_match_cli.py
    ├── test_mysql_matching.py
    ├── test_db_config.py
    ├── test_mysql_schema.py
    ├── test_recovery.py
    └── test_temp_config.py
```

v0.1은 고객·GPU 모델·서버·개별 GPU·원본 요청·원본 요구조건의 6개 테이블 SQL과 가상 seed, [Mermaid ERD 및 컬럼 설명](database/erd.md), 최소 MySQL 연결 코드 및 pytest를 제공합니다.
원본 요청과 요구조건의 변경·삭제는 DB 트리거로 차단합니다. SQL 제공과 실제 DB 적용은 구분하며 DB가 준비되지 않은 환경에서는 SQL을 실행하지 않습니다.
v0.2-1은 이 구조를 유지하며 원본 GPU 요구조건에 대한 읽기 전용 하드웨어 매칭을 추가합니다.
Flask 실행 진입점, 웹 UI, 대안 생성·고객 동의·할당·예약 기능은 후속 범위입니다.

## 하드웨어 조건 검사

```powershell
.\.venv\Scripts\python.exe -m app.match_cli 001
.\.venv\Scripts\python.exe -m app.match_cli 003 --test
```

숫자 Job ID로 MySQL의 원본 요구조건과 해당 모델의 개별 GPU·서버 상태를 조회합니다.
`ONLINE` 서버의 `HEALTHY` GPU만 집계하고, 동일 서버 필수이면 서버별 수량으로 검사합니다.
분산 허용이면 한 서버 또는 여러 서버의 수량을 합산합니다. 원본 조건은 변경하지 않습니다.

JSON 결과는 `HARDWARE_FEASIBLE` 또는 `HARDWARE_INSUFFICIENT`이며 설명, 사유 코드, 서버별 수량과 개별 장비를 포함합니다.
이는 하드웨어 수량·배치 검사 결과입니다. 할당·예약 정보를 검사하지 않으므로 실제 즉시 실행 가능 여부나 배정 확정을 뜻하지 않습니다.
잘못된 ID, 없는 Job, 원본 요구조건 누락은 별도 오류입니다. 자세한 필드와 종료 코드는 [앱 안내](app/README.md), 검증 결과는 [v0.2-1 검증 기록](docs/v0.2-1-validation.md)을 참고합니다.

## 개발 환경 준비

Python 3.11 이상을 기준으로 개발합니다. 아래는 Windows PowerShell 예시입니다.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

`.env`는 로컬에서만 값을 채우고 커밋하지 않습니다. 예시 파일에는 실제 접속 정보나 비밀값을 넣지 않습니다.
`.env`와 프로세스 환경변수에서 MySQL 접속 정보를 읽습니다. 누락된 접속 정보는 추측하지 않습니다. 의존성 설치만으로 테이블은 생성되지 않습니다.

```powershell
# Read-only connection check (requires configured MYSQL_* variables)
.\.venv\Scripts\python.exe -m app.db

# Local tests; live MySQL tests are explicitly skipped by default
.\.venv\Scripts\python.exe -m pytest -q
```

MySQL 8.0.16 이상이 필요하며, 이번 의존성 설치와 로컬 테스트는 Python 3.14에서 확인했습니다. 지원 대상으로 둔 다른 Python 버전은 아직 실행 검증하지 않았습니다.
새 빈 DB에 적용하는 방법과 별도 테스트 DB 준비는 [DB 안내](database/README.md), 실제 통합 테스트 실행은 [테스트 안내](tests/README.md)를 따릅니다.
테이블만 생성되고 트리거 생성이 실패했다면 전체 초기화를 반복하지 말고 [MySQL 부분 스키마 복구 안내](docs/mysql-recovery.md)를 확인합니다. 기본 점검 명령은 `python -m database.recover`이며 읽기 전용입니다.
관리자 트리거 생성 후 v0.1 검증을 완료했습니다. 실제 MySQL 통합 테스트 27개를 포함한 전체 pytest는 **82 passed**이며 상세 결과는 [테스트 안내](tests/README.md)에 기록했습니다. Windows 테스트는 실행마다 새 `.pytest-tmp/` 하위 경로를 사용합니다.

## 개발 방향

1. MySQL 환경을 준비해 v0.1 스키마·seed를 적용하고 실제 무결성 통합 테스트를 실행합니다.
2. v0.2-1의 하드웨어 조건 검사에 이어 원본 요청과 요구조건의 동일 트랜잭션 저장을 구현합니다.
3. 대안·고객 동의·할당 이력·실행 상태 테이블과 활성 할당을 반영한 가용성 판단을 구현합니다.
4. 대안 제시·수락 및 재검증·트랜잭션 기반 자원 배정을 구현합니다.
5. 시뮬레이션과 화면을 연결하고 동시 배정 및 조건 보존을 검증합니다.

상세 기능은 [요구사항](docs/requirements.md), 계층과 처리 흐름은 [아키텍처](docs/architecture.md), 설계 근거는 [설계 결정](docs/decisions.md)에 기록합니다.
