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

## v0.2-2 현재 구조

원본 조건의 GPU 배정·완료·실패·반환·재사용 및 이력 조회를 구현했고, 2026-11-08 재검증에서 **전체 240 passed / 실제 MySQL 83 passed**를 확인했습니다.
2026-11-08 사용자 승인과 실제 백업 복원 검증 후 개발 DB에도 v0.2-2의 할당 테이블 2개를 추가했습니다. 현재 개발·테스트 DB는 각각 8개 테이블·원본 보존 트리거 4개입니다. 기존 데이터·정의·seed는 보존했습니다.
개발 DB에서는 조회 기능만 검증했고 배정·반환·동시성 검증은 전용 테스트 DB에서 `--test`로 실행했습니다. 마이그레이션 후에도 전체 240개와 실제 MySQL 83개가 통과했습니다.
실제 실행 결과와 보존 검증은 [v0.2-2 검증 기록](docs/v0.2-2-validation.md)을 참고합니다.

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
│   ├── v0.2-2-validation.md
│   └── mysql-recovery.md
├── database/
│   ├── README.md
│   ├── __init__.py
│   ├── schema.sql
│   ├── seed.sql
│   ├── erd.md
│   ├── initialize.py
│   ├── inspect_schema.py
│   ├── migrate.py
│   ├── migrations/
│   │   ├── README.md
│   │   └── 002_transactional_allocations.sql
│   ├── recover.py
│   ├── admin_missing_triggers.sql
│   └── admin_missing_triggers_test.sql
├── app/
│   ├── README.md
│   ├── __init__.py
│   ├── db.py
│   ├── matching.py
│   ├── match_cli.py
│   ├── allocation.py
│   ├── allocation_cli.py
│   ├── repositories/
│   │   ├── hardware.py
│   │   └── allocations.py
│   └── services/
│       ├── hardware.py
│       └── allocations.py
└── tests/
    ├── README.md
    ├── conftest.py
    ├── mysql_audit.py
    ├── allocation_helpers.py
    ├── test_allocation_cli.py
    ├── test_allocation_migration.py
    ├── test_allocation_service.py
    ├── test_mysql_allocations.py
    ├── test_mysql_allocation_concurrency.py
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
v0.2-2는 기존 6개 테이블과 트리거를 유지하며 `allocations`, `allocation_gpus` 2개 테이블을 추가합니다.
Flask 실행 진입점, 웹 UI, 대안 생성·고객 동의·예약 기능은 후속 범위입니다.

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

## GPU 배정과 반환

```powershell
# 읽기 전용: 현재 가용성
.\.venv\Scripts\python.exe -m app.allocation_cli --test available 001
# 쓰기: 원본 Job 1을 배정하고 출력된 allocation_id로 정상 완료 처리
$allocation = .\.venv\Scripts\python.exe -m app.allocation_cli --test allocate 001 | ConvertFrom-Json
.\.venv\Scripts\python.exe -m app.allocation_cli --test complete $allocation.allocation_id
.\.venv\Scripts\python.exe -m app.allocation_cli --test history 1
```

완료 시 모든 GPU를 반환하고 과거 연결 행을 보존합니다. 같은 Job의 재배정 요청은 기존 결과를 반환하며 새 실행을 만들지 않습니다.
GPU를 재사용하려면 다른 Job으로 배정합니다. 자원 부족·SQL 오류는 전체 롤백하고, 모델부터 일정한 순서로 잠가 동시 중복 배정을 막습니다.
위 예시의 쓰기 명령은 테스트 DB에도 이력을 남깁니다. 검증 스위트는 seed Job 1~5에 기존 할당 이력이 있으면 보존을 위해 중단하므로, 단순 검증에는 [전용 테스트 명령](tests/README.md)을 사용합니다.
JSON 필드·종료 코드는 [앱 안내](app/README.md), DB별 적용 정책은 [마이그레이션 안내](database/migrations/README.md)를 따릅니다.

개발 DB 적용 전 백업은 Git 제외 경로 `database/backups/v0.2-2-dev-20261108T133702+0900/development-before.sql`에 보관했습니다. MySQL 공식 mysqldump 8.0.46으로 생성하고 별도 임시 MySQL에 실제 복원하여 모든 행·테이블 6개·트리거 4개의 지문 일치를 확인했습니다. 백업 내용과 인증정보는 커밋하지 않습니다.
기존 CLI의 개발 DB `--apply` 차단은 유지합니다. 이번 명시적 승인에 한해 백업·사전 검사 후 기존 `migrate(connection, apply=True)`를 별도 일회성 실행 경로에서 호출했습니다. 두 DB 모두 현재 추가 마이그레이션을 재실행할 필요가 없습니다.

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

MySQL 8.0.16 이상이 필요하며, 이번 재검증 환경은 Windows 11 / Python 3.14.6 / MySQL 8.0.46입니다. `requirements.txt`의 네 패키지 버전과 설치 버전이 일치하고 `pip check`가 통과했습니다. 지원 대상으로 둔 다른 Python 버전은 아직 실행 검증하지 않았습니다.
새 빈 DB에 적용하는 방법과 별도 테스트 DB 준비는 [DB 안내](database/README.md), 실제 통합 테스트 실행은 [테스트 안내](tests/README.md)를 따릅니다.
테이블만 생성되고 트리거 생성이 실패했다면 전체 초기화를 반복하지 말고 [MySQL 부분 스키마 복구 안내](docs/mysql-recovery.md)를 확인합니다. 기본 점검 명령은 `python -m database.recover`이며 읽기 전용입니다.
현재 전체 검증은 `python -m pytest -q --run-mysql`로 **240 passed**입니다. 기본 실행은 **157 passed, 83 skipped**이며 MySQL 성공으로 계산하지 않습니다. Windows 테스트는 실행마다 새 `.pytest-tmp/` 하위 경로를 사용합니다.

### VS Code와 PowerShell

Python 확장(`ms-python.python`), Pylance, Python Environments가 설치되어 있습니다. 이번 로컬 `.vscode/settings.json`은 기존 환경 관리자·pip·envFile 설정을 보존하고 다음 워크스페이스 설정을 추가했습니다. `.vscode/`는 Git 제외 대상이며 다른 PC에 자동 배포되지 않습니다.

```json
{
  "python.defaultInterpreterPath": "${workspaceFolder}/.venv/Scripts/python.exe",
  "python.testing.pytestEnabled": true,
  "python.testing.unittestEnabled": false,
  "python.testing.pytestArgs": ["tests"]
}
```

기존에 선택한 인터프리터는 이 기본 경로 설정만으로 바뀌지 않을 수 있습니다. `Ctrl+Shift+P` → `Python: Select Interpreter` → `Enter interpreter path...` → `Find...`에서 프로젝트의 `.venv\Scripts\python.exe`를 선택합니다. Python 파일을 열어 상태 표시줄의 환경도 확인합니다. 자세한 동작은 [VS Code 공식 설정 문서](https://code.visualstudio.com/docs/python/settings-reference)를 따릅니다.

Windows PowerShell의 유효 실행 정책 `Restricted`가 `Documents/WindowsPowerShell/profile.ps1` 로드를 막는 PSSecurityException / UnauthorizedAccess를 재현했습니다. 같은 터미널에서 위 Python 직접 호출은 성공합니다. `(.venv)` 표시는 편집기의 인터프리터 선택을 보장하지 않습니다.
가상환경 활성화 스크립트 없이 위 명령을 실행할 수 있으므로 실행 정책·사용자 프로필·ACL·사용자 전체 VS Code 설정은 변경하지 않았습니다. 새 PowerShell 세션에는 프로필 오류가 계속 표시될 수 있습니다.

## 개발 방향

1. 원본 요청과 요구조건을 같은 트랜잭션에 저장하는 등록 서비스를 구현합니다.
2. 대안·고객 동의 모델과 제시·수락 흐름을 설계하고 기존 배정 서비스에 연결합니다.
3. 명시적 재실행 정책, 장비 운영 변경의 잠금 정책을 정한 뒤 필요한 범위만 확장합니다.
4. 실행 시뮬레이션과 Flask 화면을 연결합니다. 이번 마이그레이션 작업에서는 후속 기능 구현을 시작하지 않았습니다.

상세 기능은 [요구사항](docs/requirements.md), 계층과 처리 흐름은 [아키텍처](docs/architecture.md), 설계 근거는 [설계 결정](docs/decisions.md)에 기록합니다.
