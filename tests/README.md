# 테스트

## 현재 재검증 결과 (2026-11-10, v0.3-2)

Windows 11 / 프로젝트 `.venv` Python 3.14.6 / pytest 9.2.0 / MySQL 8.0.46에서 직접 재실행했다.

| 실행 | 결과 |
| --- | --- |
| `python -m pytest -q` | **250 passed, 162 skipped** |
| `python -m pytest -q --run-mysql` | **412 passed** |
| `python -m pytest -q -m mysql --run-mysql` | **162 passed, 250 deselected** |

전체는 로컬 250개 + 실제 MySQL 162개다. 기존 364개를 유지하고 신규 48개를 추가했다. 신규는 basis_migration 15, accepted_allocation_cli 5, mysql_accepted_allocations 21, mysql_accepted_concurrency 7개다. 기존 fixture의 허용 스키마를 11개까지 확장하고 정확한 임시 할당 ID 정리에 새 근거 행을 포함했다. 기존 업무 검증은 제거하지 않았다.
전체/통합 실행은 실패·오류·skip·경고가 없다. 기본 실행의 skip은 MySQL 미실행이다. 아래 이전 버전 절은 과거 이력이며 최신 근거는 [v0.3-2 검증 기록](../docs/v0.3-2-validation.md)을 따른다.

저장소 루트에서 설치된 가상환경으로 실행한다.

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

기본 실행은 환경 설정, DB 분리 정책, 지원 버전, 비밀정보 비노출 및 초기화 안전장치를 검증한다. MySQL에 접속하지 않는다.
통합 테스트는 수집되지만 `--run-mysql` 옵션이 없으면 명시적으로 skip한다. 이 결과는 MySQL 제약 검증 성공을 의미하지 않는다.

## 실제 MySQL 통합 테스트

1. 개발 DB와 다른 전용 테스트 DB 및 계정을 발급받는다. 이름은 `_test`로 끝나야 한다.
2. `.env`에 `TEST_MYSQL_HOST/PORT/DATABASE/USER/PASSWORD`를 모두 설정한다. 개발용 값으로 대체하지 않는다.
3. 새 빈 테스트 DB만 [DB 안내](../database/README.md)에 따라 기본 schema·트리거·seed를 한 번 준비하고 [추가 마이그레이션](../database/migrations/README.md) 002·003·004로 11개 테이블을 준비한다. 현재 테스트 DB는 이미 준비되어 있으므로 재적용하지 않는다. 개발 DB에는 003·004를 적용하지 않는다.
4. 연결 확인 후 테스트를 실행한다.

```powershell
.\.venv\Scripts\python.exe -m app.db --test
.\.venv\Scripts\python.exe -m pytest -q --run-mysql
```

명시적으로 통합 테스트를 켠 경우 설정·연결·스키마 문제는 skip하지 않고 실패로 처리한다.
검증 대상은 총 11개 테이블·20개 FK의 참조 관계, PK/UNIQUE/CHECK, GPU 수량·배치·상태, 원본 보호와 배정·반환·제안·응답·배정 근거·동시성이다. 업무 규칙과 직접 SQL 제약 검증을 구분한다.
SQLite나 모의 객체 결과를 MySQL 무결성 검증으로 취급하지 않는다.

일반 테스트는 UUID가 포함된 자기 데이터만 INSERT하고 끝나면 전체 ROLLBACK한다. 원본 UPDATE/DELETE 거부 테스트도 해당 테스트가 만든 행에만 시도한다. 기존 행을 수정하거나 삭제하지 않는다.
`test_mysql_allocations.py`는 외부 트랜잭션 안의 SAVEPOINT로 서비스 경계를 감싸 실제 SQL·제약·부분 실패 롤백을 검증한다. 삭제 금지 원본 Job을 커밋하지 않기 위한 정책이다.

`test_mysql_allocation_concurrency.py`의 6개 테스트는 별도 연결의 실제 잠금 대기·COMMIT·ROLLBACK과 실제 CLI 하위 프로세스를 검증한다. 변경하지 않은 seed Job 1~5를 참조하는 임시 할당만 커밋하고, 등록한 정확한 allocation_id의 자식·헤더만 finally에서 정리한다. DB별 advisory lock으로 지원 테스트 실행을 분리하고, 기존 seed 할당 이력이 있으면 쓰기 전에 중단한다. 다른 테스트나 앱 쓰기와 동시에 실행하지 않는다.
테스트는 DB나 테이블을 생성·삭제·초기화하지 않는다. 이번 스위트 실행 전후 전체 행·논리 스키마·트리거 지문을 비교했다. 자동 증가 다음 번호는 롤백해도 돌아가지 않으므로 비교에서 제외하며 번호 간격이 생길 수 있다.

v0.2-2 안정화 당시에는 스위트 외에도 실제 서비스 연결에서 첫 GPU INSERT 후 두 번째 FK 오류, GPU 반환 UPDATE 후 헤더 CHECK 오류를 각각 주입해 외부 트랜잭션의 전체 롤백을 확인했다. 당시 두 시나리오는 pytest 통과 수에 더하지 않았다.
독립 읽기 전용 감사 연결에서 개발·테스트 DB 모두 전후 지문과 모든 seed 값을 비교했고 일치했다. 감사는 `tests/mysql_audit.py`의 `snapshot(connection, version)`을 사용한다. 개발 DB에는 INSERT하지 않았다.

## v0.3-1 임시 데이터·동시성 정책

`test_mysql_proposals.py` 42개는 기존 seed를 읽거나 UUID 임시 Job·장비를 외부 트랜잭션에 만들고 SAVEPOINT 경계로 서비스 SQL을 검증한 뒤 전체 롤백한다. 생성·수락·거절 전후 기존 8개 테이블 지문을 비교해 GPU 점유·원본 보존을 확인한다.
`test_mysql_proposal_concurrency.py` 9개는 실제 별도 연결의 잠금 대기·커밋·롤백, 직접 SQL의 수락 UNIQUE, 실제 CLI를 검증한다. seed Job을 변경하지 않고 테스트가 만든 Proposal ID만 등록해 finally에서 그 응답·제안만 정리한다. 실제 외부 트랜잭션의 두 번째 제안 INSERT 오류 및 응답 INSERT 뒤 CHECK 오류 롤백도 포함한다.
이 테스트는 기존 배정 동시성 테스트와 같은 `_v022_seed_tests` advisory lock을 사용한다. seed Job에 기존 제안 이력이 있으면 삭제하지 않고 중단한다. 다른 테스트/앱 쓰기와 병렬 실행하지 않는다. 수동 CLI로 기록한 이력을 전체 삭제하거나 초기화해 테스트를 맞추지 않는다.
개발 DB는 v0.2-2의 8개 그대로이고 모든 행·정의·트리거 지문이 보존됐다. 테스트 DB는 10개이며 테스트 종료 후 신규 제안·응답은 0행, 기존 8개 지문은 원래와 같다. 자동 증가 번호 간격은 기존 정책대로 허용한다.

## 이번 환경의 검증 기록 (2026-11-03)

- Windows / Python 3.14.6 / pytest 9.2.0에서 기본 실행 결과: **20 passed, 27 skipped**.
- 27개는 실제 MySQL이 필요한 통합 테스트이며 통과로 계산하지 않는다. `.env`와 MYSQL_*/TEST_MYSQL_* 접속 환경변수가 없어 실행하지 않았다.
- 개발·테스트 연결 확인 CLI는 각각 필요한 변수 누락을 보고하고 종료 코드 1로 종료했다. 서버 연결 시도, schema·seed 실행, 테이블 생성은 수행하지 않았다.
- Python 컴파일, 의존성 검사(`pip check`), Git 공백 검사, 로컬 문서 링크, SQL 파일 구성과 seed PK/FK 참조 관계의 정적 검사는 통과했다.
- 정적 검사는 MySQL 서버의 SQL 문법·CHECK·트리거 실행 검증을 대체하지 않는다. 실제 PK/FK/UNIQUE/CHECK 및 원본 보존 검증은 테스트 DB 준비 후 `--run-mysql`로 수행해야 한다.

## 부분 스키마 복구 변경 검증 (2026-11-03)

기본 pytest는 **53 passed, 27 skipped**다. `test_recovery.py`는 오류 필드 비노출, 빈 DB 정책 유지, 부분 스키마 보호, 정의 불일치 거부, 누락 트리거만 추가, 기존 데이터 seed 거부, seed 실패 롤백 및 관리자 SQL 내보내기를 모의 연결로 검증한다.
실제 MySQL에서는 개발 DB 6개 테이블·0개 트리거·각 테이블 0행과 테스트 DB의 빈 상태를 읽기 전용으로 확인했다. 개발 DB 첫 트리거 생성은 errno=1419 / SQLSTATE=HY000으로 실패했다.
이 권한 문제에서 DB 쓰기를 중단했으며 통합 테스트 27개는 실행하지 않았다. 관리자 작업 후 [복구 절차](../docs/mysql-recovery.md)에 따라 테스트 DB를 준비하고 `--run-mysql`을 실행해야 한다.

## 관리자 복구 후 최종 결과 (2026-11-03)

이 절은 v0.1 관리자 복구 당시 결과다. 현재 v0.2-2 결과는 문서 첫 절을 따른다.

```text
python -m pytest -q --run-mysql
82 passed
```

Windows / Python 3.14.6 / pytest 9.2.0 / MySQL 8.0.46에서 Codex가 직접 실행했다. 총 82개는 로컬 테스트 55개와 실제 MySQL 통합 테스트 27개이며 skip·실패·오류는 없다.
실제 테스트는 PK 6개, FK 5개, UNIQUE, GPU 수량·상태·배치 CHECK, 서버/GPU와 고객/원본 요구조건 연결, 원본 UPDATE/DELETE 거부를 포함한다.

테스트 DB에는 모든 테이블이 비었음을 확인한 뒤 기존 복구 도구로 seed를 입력했다. 종료 후 행 수와 seed에 명시된 모든 값을 다시 조회해 추가 테스트 행이 남지 않았음을 확인했다. 개발 DB는 읽기 전용으로만 확인했고 변경하지 않았다.

제한된 계정에서 MySQL이 FK 위반을 1452 대신 1216으로 반환해 처음 5개 테스트가 실패했다. 둘 다 같은 무결성 위반임을 공식 문서로 확인하고, 이 두 코드와 SQLSTATE 23000 및 거부된 행의 미저장을 검사하도록 수정했다. DB 권한·제약은 바꾸지 않았다.

Windows의 과거 PermissionError는 현재 재현되지 않았다. 실행 환경의 사용자 임시 폴더 권한 차이에 의존하지 않도록 기본 테스트는 `.pytest-tmp/run-<무작위값>/tmp`의 새로운 경로를 사용한다. 기존 임시 파일을 삭제하거나 ACL을 변경하지 않는다. 명시적인 `--basetemp` 설정은 유지하며, 경로 분리와 기존 파일 보존을 테스트 2개로 검증한다.
이전 실행 디렉터리는 자동 삭제하지 않고 `.pytest-tmp/` 전체를 Git에서 제외한다. 상세 이력은 [복구 기록](../docs/mysql-recovery.md)을 따른다.

## v0.2-1 최종 결과 (2026-11-05)

```text
python -m pytest -q
107 passed, 51 skipped

python -m pytest -q --run-mysql
158 passed
```

전체 158개는 기존 82개와 신규 76개다. 로컬 테스트 107개, 실제 MySQL 통합 테스트 51개(기존 27개 + 신규 24개)가 모두 통과했다. 통합 실행에는 skip·실패·경고가 없다.

- `test_hardware_matching.py`: 모델 일치, 상태 9조합, 동일 서버·분산 수량, 빈 장비, ID 검증과 매개변수 전달을 검증한다.
- `test_match_cli.py`: JSON 결과, 별도 오류 코드, 개발/테스트 설정 선택, 읽기 전용 스냅샷, 종료 시 롤백·닫기와 DB 오류 비밀정보 비노출을 검증한다.
- `test_mysql_matching.py`: 실제 seed 5건, 임시 장비의 상태 9조합, 동일 서버·분산 수량, 모델 불일치, 없는 Job과 요구조건 누락, SELECT 전용 인터페이스 및 판단 전후 DB 보존을 검증한다.
- `mysql_audit.py`: 모든 행(생성 시각 포함), 테이블 정의와 트리거 정의의 SHA-256 지문을 계산한다. 자동 증가 다음 번호는 스키마 비교에서 제외한다.

개발·테스트 DB에서 실제 CLI로 Job 001~005를 각각 조회해 기대 결과와 일치함을 확인했다.
신규 테스트 실행 전후 별도 읽기 전용 연결에서 두 DB의 전체 행·테이블 정의·트리거 지문이 모두 같음을 확인했다. seed와 기존 행은 변경하지 않았고 임시 행은 모두 롤백했다.
InnoDB의 자동 증가 카운터는 롤백해도 되돌아가지 않아 테스트 DB의 다음 번호에는 간격이 생길 수 있다. 개발 DB에는 INSERT하지 않았다.
상세 결과와 실행 명령은 [v0.2-1 검증 기록](../docs/v0.2-1-validation.md)에 정리했다.

## v0.4-1 신규 Job 등록 검증

```powershell
.\.venv\Scripts\python.exe -B -m pytest -q --run-mysql -p no:cacheprovider
.\.venv\Scripts\python.exe -B -m pytest -q -m mysql --run-mysql -p no:cacheprovider
```

기존 412개를 유지하고 94개(단위 60개·MySQL 34개)를 추가했다.
`test_job_service.py`, `test_job_cli.py`, `test_mysql_jobs.py`가 입력 경계·원자성·조회·기존 서비스 호환성을 검증한다.
신규 불변 Job은 실제 커밋하지 않는다. 기존 RollbackEnvelope 또는 독립 연결의 커밋 경계 롤백으로 테스트 데이터를 남기지 않으며 트리거는 계속 활성화한다.
조회 CLI만 실제 하위 프로세스로 검증한다. 등록 CLI의 영구 커밋 후 재조회 제한, 동시성 검증 방식과 전체 DB 보존 결과는 [검증 기록](../docs/v0.4-1-validation.md)에 설명한다.
