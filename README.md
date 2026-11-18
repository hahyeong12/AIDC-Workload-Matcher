# AIDC-Workload-Matcher

컴퓨터공학과 데이터베이스 텀프로젝트로 개발하는 AI 데이터센터(AIDC) 작업 매칭 및 자원 배정 시스템입니다.
고객의 AI 학습 작업 요청을 데이터센터의 실제 GPU 자원과 운영 조건에 비교하여 실행 가능성을 판단하고, 가능한 실행 계획과 대안을 제시합니다.

## 설계 로드맵과 협업 기준

- [PROJECT_MASTER_PLAN.md](docs/PROJECT_MASTER_PLAN.md): 승인된 상위 방향·M0~M5 로드맵·개별 승인 대상인 복합 자원 설계.
- [요구사항](docs/requirements.md) · [아키텍처](docs/architecture.md) · [설계 결정](docs/decisions.md) · [실제 ERD](database/erd.md): 현행 구현의 상세 정본.
- [AGENTS.md](AGENTS.md) · [CLAUDE.md](CLAUDE.md): 에이전트 작업·독립 리뷰 지침.
- [기기 전환 시작 루틴](docs/ENVIRONMENT_HANDOFF.md): 노트북·데스크톱에서 Git·Python·DB 진행 수준을 먼저 대조하는 절차.
- [M0-B 시연 검증 계획](docs/m0-validation.md): 백업 복원 검증 이후 별도 승인할 영구 COMMIT·독립 연결 조회 절차(실행 전).

**현재 구현은 GPU 중심 v0.4-2입니다.** GPU·CPU·RAM을 위한 추가 3개 테이블은 아직 제안으로, 문서 반영이 DB 변경 허가는 아닙니다. 노트북 시연 DB는 M0-A 구성·seed 입력을 완료했으며, 백업 복원 검증과 M0-B 영구 COMMIT 시연은 남아 있습니다. 기기별 검증 날짜와 Git·Python·MySQL 상태는 [환경 전환 시작 루틴](docs/ENVIRONMENT_HANDOFF.md)에 모아 기록합니다.

### 현재 노트북·데스크톱 Python 실행 경로

OneDrive 밖 각 컴퓨터의 **독립된 가상환경**을 사용합니다. 프로젝트 루트에서 아래를 먼저 실행해 현재 PowerShell 세션에 `$projectPython`을 지정하면 이 README의 모든 실행 예시를 그대로 사용할 수 있습니다.

```powershell
$venvName = 'AIDC-Workload-Matcher-laptop'  # 데스크톱일 때는 'AIDC-Workload-Matcher-desktop'
$projectPython = "$env:LOCALAPPDATA\venvs\$venvName\Scripts\python.exe"
& $projectPython --version
```

기기별 전용 venv가 없으면 먼저 [환경 전환 시작 루틴](docs/ENVIRONMENT_HANDOFF.md)에 따라 생성·설치합니다. 활성화된 Anaconda `(base)`의 `python`을 대신 사용하지 마세요. 아래 테스트 DB 대상 쓰기 CLI 예시는 이력에 영향을 줄 수 있으므로 **별도 승인·격리 없이 실행하지 않습니다**.

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

## v0.4-2 현재 구조

Flask·Jinja2 한국어 화면에서 고객 선택, 자원·Job 조회, 등록 확인, 매칭, 대안 비교·동의, 배정·반환 이력을 제공합니다.
**웹 쓰기는 기본 차단**이며 현재 8개 테이블의 개발 DB는 읽기 전용으로 사용합니다.

```powershell
# 위 '현재 노트북·데스크톱 Python 실행 경로'에서 $projectPython 설정 후
$env:WEB_ENABLE_WRITES = '0'
& $projectPython -B -m app.web
```

접속 주소: **http://127.0.0.1:5000/**. Debug·자동 초기화·자동 마이그레이션은 사용하지 않습니다.
쓰기 활성화 조건과 003·004의 별도 승인·백업 절차는 [v0.4-2 검증 기록](docs/v0.4-2-validation.md)에 있습니다.
테스트 DB는 수동 웹 시연 대상으로 사용하지 않습니다. 고객 선택은 실제 인증이 아닙니다.

`app.job_cli`로 신규 요청·명시적 GPU 조건을 원자적으로 등록하고 Job 상세·고객별 목록을 조회합니다.
원본 문장은 그대로 보존하며 기존 매칭·대안·동의·배정 서비스에 새 ID를 전달합니다.
[신규 등록 사용법과 검증 기록](docs/v0.4-1-validation.md)을 참고하세요.

고객이 수락한 대안 조건으로 실제 GPU를 배정하고 원본 Job·Proposal·ACCEPTED 응답·할당을 연결합니다. **대안 생성·수락은 GPU를 예약하지 않습니다.** 실제 배정 시 잠금 후 현재 가용성을 다시 검사합니다. 기존 원본 조건 배정과 완료·실패·반환 기능을 유지합니다. CLI 고객 ID는 관계 검사용이며 실제 인증이 아닙니다.

004의 `allocation_bases`는 ORIGINAL / ACCEPTED_PROPOSAL 근거, 고객·Proposal·응답 ID와 적용 모델·수량·배치 조건을 저장합니다. 기존 10개 테이블은 ALTER하거나 백필하지 않습니다. 개발 DB는 **8개 테이블·트리거 4개**, 테스트 DB는 **11개 테이블·트리거 4개**입니다. 개발 DB에는 003·004를 적용하지 않았습니다.

기존 506개 테스트를 보존했습니다. 이전 등록 검증은 [v0.4-1 기록](docs/v0.4-1-validation.md), 최신 웹 검증은 [v0.4-2 기록](docs/v0.4-2-validation.md)에 있습니다. 실제 GPU 연산·예약·과금·실행시간 예측·인증은 이번 범위에 없습니다.

```powershell
# 아래 쓰기는 전용 테스트 DB 예시입니다. P는 이미 수락한 Proposal ID입니다.
& $projectPython -m app.allocation_cli --test allocate 001
& $projectPython -m app.allocation_cli --test allocate-proposal 003 --proposal-id P --customer-id 2
& $projectPython -m app.allocation_cli --test detail A
& $projectPython -m app.allocation_cli --test job-history 003
& $projectPython -m app.allocation_cli --test history G
& $projectPython -m app.allocation_cli --test complete A
```

A는 할당 ID, G는 GPU ID입니다. `detail`은 근거·적용 조건·실제 GPU·배정/반환 시각을 함께 반환합니다. `job-history`는 Job의 과거 할당, `history`는 GPU의 사용 이력을 조회합니다. 같은 Job·같은 근거의 반복 배정은 종료 후에도 기존 결과를 반환하고 다른 근거는 충돌로 처리합니다. 예시를 직접 실행하면 새 이력이 남으므로 통합 테스트 전 전용 DB의 상태를 확인하세요.

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
│   ├── v0.3-1-validation.md
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
│   ├── proposal_migrate.py
│   ├── basis_migrate.py
│   ├── migrations/
│   │   ├── README.md
│   │   ├── 002_transactional_allocations.sql
│   │   ├── 003_workload_proposals.sql
│   │   └── 004_allocation_bases.sql
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
│   ├── proposal.py
│   ├── proposal_cli.py
│   ├── repositories/
│   │   ├── hardware.py
│   │   ├── allocations.py
│   │   └── proposals.py
│   └── services/
│       ├── hardware.py
│       ├── allocations.py
│       └── proposals.py
└── tests/
    ├── README.md
    ├── conftest.py
    ├── mysql_audit.py
    ├── allocation_helpers.py
    ├── proposal_helpers.py
    ├── test_proposal_cli.py
    ├── test_proposal_migration.py
    ├── test_proposal_service.py
    ├── test_mysql_proposals.py
    ├── test_mysql_proposal_concurrency.py
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
v0.3-1은 `job_proposals`, `proposal_responses`를 추가해 대안과 고객 의사를 분리해 보존합니다.
Flask 실행 진입점·웹 UI는 v0.4-2에서 제공합니다. 미래 예약은 후속 범위입니다. 수락 대안의 실제 GPU 배정은 v0.3-2에서 제공합니다.

## 하드웨어 조건 검사

```powershell
& $projectPython -m app.match_cli 001
& $projectPython -m app.match_cli 003 --test
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
& $projectPython -m app.allocation_cli --test available 001
# 쓰기: 원본 Job 1을 배정하고 출력된 allocation_id로 정상 완료 처리
$allocation = & $projectPython -m app.allocation_cli --test allocate 001 | ConvertFrom-Json
& $projectPython -m app.allocation_cli --test complete $allocation.allocation_id
& $projectPython -m app.allocation_cli --test history 1
```

완료 시 모든 GPU를 반환하고 과거 연결 행을 보존합니다. 같은 Job의 재배정 요청은 기존 결과를 반환하며 새 실행을 만들지 않습니다.
GPU를 재사용하려면 다른 Job으로 배정합니다. 자원 부족·SQL 오류는 전체 롤백하고, 모델부터 일정한 순서로 잠가 동시 중복 배정을 막습니다.
위 예시의 쓰기 명령은 테스트 DB에도 이력을 남깁니다. 검증 스위트는 seed Job 1~5에 기존 할당 이력이 있으면 보존을 위해 중단하므로, 단순 검증에는 [전용 테스트 명령](tests/README.md)을 사용합니다.
JSON 필드·종료 코드는 [앱 안내](app/README.md), DB별 적용 정책은 [마이그레이션 안내](database/migrations/README.md)를 따릅니다.

개발 DB 적용 전 백업은 Git 제외 경로 `database/backups/v0.2-2-dev-20261108T133702+0900/development-before.sql`에 보관했습니다. MySQL 공식 mysqldump 8.0.46으로 생성하고 별도 임시 MySQL에 실제 복원하여 모든 행·테이블 6개·트리거 4개의 지문 일치를 확인했습니다. 백업 내용과 인증정보는 커밋하지 않습니다.
기존 CLI의 개발 DB `--apply` 차단은 유지합니다. v0.2-2에 대한 당시 명시적 승인 범위에서만 기존 `migrate(connection, apply=True)`를 호출했습니다. 002는 재실행하지 않으며 새 003은 테스트 DB에만 적용했습니다. 개발 DB의 003 적용에는 별도 승인이 필요합니다.

## 대안 생성과 고객 응답

현재는 003이 적용된 테스트 DB에서 `--test`를 명시합니다.

```powershell
$result = & $projectPython -m app.proposal_cli --test generate 003 | ConvertFrom-Json
& $projectPython -m app.proposal_cli --test list 003
& $projectPython -m app.proposal_cli --test show $result.proposals[0].proposal_id
# seed Job 003의 고객 ID는 2. 이 입력은 실제 인증이 아님
& $projectPython -m app.proposal_cli --test accept 003 $result.proposals[0].proposal_id 2
& $projectPython -m app.proposal_cli --test reject 003 $result.proposals[1].proposal_id 2
& $projectPython -m app.proposal_cli --test history 003
```

seed Job 003에는 H100 4개 분산 허용 / H100 3개 동일 서버 대안을, Job 005에는 A100 2개 분산 허용 대안을 확인했습니다. 원본 조건을 이미 만족하면 대안을 만들지 않으며 가용 GPU가 0개이면 수량 축소도 생성하지 않습니다.
분산 학습 지원 여부와 수량 축소의 성능·시간·품질 영향은 검증되지 않았습니다. 모델 대체·시간 연장·epoch 축소·성능 예측은 구현하지 않았습니다.
같은 조건은 기존 제안 ID·시각·응답을 보존합니다. 같은 응답은 멱등적으로 반환하고 반대 응답은 충돌이며 Job당 수락은 최대 한 건입니다. 할당 이력이 있는 Job은 새 대안 생성과 새 수락을 거부합니다(거절과 기존 응답 재사용은 가능). 수락 대안의 배정이 실패하면 같은 대안 재시도·원본 배정·새 Job 등록 중 선택합니다([F1 검증 기록](docs/f1-allocation-history-validation.md)). 수락된 조건은 v0.3-2 배정 시 다시 검증해야 합니다.
위 CLI는 제안·응답 이력을 남깁니다. 기존 seed 이력이 있으면 동시성 테스트는 삭제하지 않고 중단하므로, 임시 데이터 정리까지 포함한 검증에는 [전체 pytest](tests/README.md)를 사용합니다.

## 개발 환경 준비

Python 3.11 이상을 기준으로 개발합니다. 아래는 Windows PowerShell 예시입니다.

```powershell
# 각 컴퓨터의 프로젝트 전용 venv가 이미 준비됐다면 재생성하지 않습니다.
& $projectPython -m pip install -r requirements.txt
& $projectPython -m pip check

# .env가 없는 신규 환경에서만 예제 파일을 복사합니다.
if (!(Test-Path .env)) { Copy-Item .env.example .env }
```

`.env`는 로컬에서만 값을 채우고 커밋하지 않습니다. 예시 파일에는 실제 접속 정보나 비밀값을 넣지 않습니다.
`.env`와 프로세스 환경변수에서 MySQL 접속 정보를 읽습니다. 누락된 접속 정보는 추측하지 않습니다. 의존성 설치만으로 테이블은 생성되지 않습니다.

```powershell
# Read-only connection check (requires configured MYSQL_* variables)
& $projectPython -m app.db

# Local tests; live MySQL tests are explicitly skipped by default
& $projectPython -m pytest -q
```

MySQL 8.0.16 이상이 필요하며, 이번 재검증 환경은 Windows 11 / Python 3.14.6 / MySQL 8.0.46입니다. `requirements.txt`의 네 패키지 버전과 설치 버전이 일치하고 `pip check`가 통과했습니다. 지원 대상으로 둔 다른 Python 버전은 아직 실행 검증하지 않았습니다.
새 빈 DB에 적용하는 방법과 별도 테스트 DB 준비는 [DB 안내](database/README.md), 실제 통합 테스트 실행은 [테스트 안내](tests/README.md)를 따릅니다.
테이블만 생성되고 트리거 생성이 실패했다면 전체 초기화를 반복하지 말고 [MySQL 부분 스키마 복구 안내](docs/mysql-recovery.md)를 확인합니다. 기본 점검 명령은 `python -m database.recover`이며 읽기 전용입니다.
현재 전체 검증은 `python -m pytest -q --run-mysql`로 **599 passed**입니다. MySQL 전용은 **217 passed, 382 deselected**입니다. v0.4-2에서 93개(단위 72개·MySQL 21개)를 추가하고 기존 506개를 보존했습니다. 기본 실행의 MySQL skip은 성공으로 계산하지 않습니다. Windows 테스트는 실행마다 새 `.pytest-tmp/` 하위 경로를 사용합니다.

### VS Code와 PowerShell

Python 확장(`ms-python.python`), Pylance, Python Environments가 설치되어 있습니다. 이번 로컬 `.vscode/settings.json`은 기존 환경 관리자·pip·envFile 설정을 보존하고 다음 워크스페이스 설정을 추가했습니다. `.vscode/`는 Git 제외 대상이며 다른 PC에 자동 배포되지 않습니다.

```json
{
  "python.testing.pytestEnabled": true,
  "python.testing.unittestEnabled": false,
  "python.testing.pytestArgs": ["tests"]
}
```

Python 인터프리터 경로는 기기마다 다르므로 공통 `python.defaultInterpreterPath`를 강제로 설정하지 않습니다. VS Code의 `Python: Select Interpreter`에서 이 기기의 `%LOCALAPPDATA%\\venvs\\AIDC-Workload-Matcher-<laptop|desktop>\\Scripts\\python.exe`를 선택합니다.

VS Code에서 `Ctrl+Shift+P` → `Python: Select Interpreter` → `Enter Interpreter Path...`로 해당 기기의 전용 venv 실행 파일을 지정합니다. Python 파일을 열어 상태 표시줄의 환경을 확인합니다. 자세한 동작은 [VS Code 공식 설정 문서](https://code.visualstudio.com/docs/python/settings-reference)를 참고합니다.

Windows PowerShell의 유효 실행 정책 `Restricted`가 `Documents/WindowsPowerShell/profile.ps1` 로드를 막는 PSSecurityException / UnauthorizedAccess를 재현했습니다. 같은 터미널에서 위 Python 직접 호출은 성공합니다. `(base)` 같은 터미널 표시도 편집기의 인터프리터 선택을 보장하지 않습니다.
가상환경 활성화 스크립트 없이 위 명령을 실행할 수 있으므로 실행 정책·사용자 프로필·ACL·사용자 전체 VS Code 설정은 변경하지 않았습니다. 새 PowerShell 세션에는 프로필 오류가 계속 표시될 수 있습니다.

## 개발 방향

1. 원본 요청·요구조건의 원자적 등록(v0.4-1)과 안전한 Flask 화면 연결(v0.4-2)을 완료했습니다.
2. v0.3-2의 수락 대안 배정·응답 근거 추적·실제 배정 직전 재검증은 완료했습니다. 실제 인증·운영 권한 분리와 Job 재실행 정책은 후속 설계로 남깁니다.
3. 명시적 재실행 정책, 장비 운영 변경의 잠금 정책을 정한 뒤 필요한 범위만 확장합니다.
4. 별도 승인·백업 후 개발 DB의 003·004 적용을 검토합니다. 실제 인증·예약·과금·학습 실행은 이번 구현에 포함하지 않았습니다.

상세 기능은 [요구사항](docs/requirements.md), 계층과 처리 흐름은 [아키텍처](docs/architecture.md), 설계 근거는 [설계 결정](docs/decisions.md)에 기록합니다.
