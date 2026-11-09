# 버전별 추가 마이그레이션

| DB | 현재 적용 버전 | 테이블 / 원본 보호 트리거 |
| --- | --- | --- |
| 개발 `aidc_workload_matcher` | v0.2-2 | 8개 / 4개, 003 미적용 |
| 테스트 `aidc_workload_matcher_test` | v0.3-2 | 11개 / 4개 |

기본 schema.sql, 002, 003은 별도 자산이다. 이미 적용된 초기화나 이전 마이그레이션은 재실행하지 않는다. 아래 002 적용 이력과 후반의 003 절을 구분한다.

## 002: v0.2-2

[002_transactional_allocations.sql](002_transactional_allocations.sql)은 allocations / allocation_gpus만 생성한다. 기존 데이터가 있는 DB에 schema.sql을 다시 실행하지 않는다.
2026-11-08 **사용자 승인 후 개발 DB에도 002 적용을 완료**했다. 그 직후 개발·테스트 DB는 모두 v0.2-2의 8개 테이블·원본 보존 트리거 4개였다. 기존 6개 테이블의 모든 행·정의·seed와 트리거는 그대로 유지했다.
최초 코드 재검증 당시 개발 DB는 6개 테이블이었고, 이번 승인된 적용에서 2개만 추가했다. 기존 8개 테이블인 테스트 DB에는 마이그레이션을 재실행하지 않았다. [실제 검증 기록](../../docs/v0.2-2-validation.md)을 참고한다.

## 사전 검사와 테스트 DB 적용

```powershell
# 각각 개발 DB / 테스트 DB의 읽기 전용 사전 검사
.\.venv\Scripts\python.exe -m database.migrate
.\.venv\Scripts\python.exe -m database.migrate --test

# 아직 추가 테이블이 없는 새 테스트 DB에만 적용. 현재 테스트 DB에서는 실행하지 않음
.\.venv\Scripts\python.exe -m database.migrate --test --apply
```

TEST_MYSQL_* 전체 설정, `_test` 이름과 개발 DB와 다른 이름이라는 기존 정책을 따른다. MYSQL_* 값으로 대체하지 않는다.
사전에 운영 중인 DB 쓰기·별도 DDL을 중지하고 필요한 백업을 Git 제외 경로에 보관한다. 이 도구는 DB 생성·삭제, 권한 변경, 서버 전역 설정 변경을 수행하지 않는다.

도구는 다음을 확인한 뒤에만 CREATE TABLE을 실행한다.

1. 호출자의 진행 중인 트랜잭션이 없는 전용 연결이다.
2. 지원 MySQL 버전이고 기존 6개 InnoDB 테이블 정의가 원본과 일치한다.
3. 원본 보존 트리거 4개가 존재하며 정의가 일치한다.
4. 알 수 없는 테이블·뷰·트리거·루틴·이벤트가 없다.
5. 기존 추가 테이블이 있다면 정의가 정확히 일치한다. 완전한 8개 구성이면 DDL 없이 검증만 한다.

DB별 advisory lock으로 지원 마이그레이션 도구의 동시 실행을 차단한다. 이 잠금은 임의의 외부 DDL이나 직접 DML까지 막지는 않으므로 점검 시간의 쓰기 중지가 필요하다.
기존 6개 테이블의 모든 값·정의와 트리거의 지문을 전후 비교한다. seed를 입력·수정·삭제하지 않는다. 자동 증가 다음 번호는 데이터나 논리 스키마 비교 대상에서 제외한다.

새로운 빈 DB도 먼저 기존 초기화·관리자 트리거 준비 절차로 v0.1을 완성한 후 이 추가 마이그레이션을 적용한다. schema.sql은 계속 6개 테이블 기준의 최초 초기화 파일이다.

## 부분 실패와 재실행

MySQL의 [DDL 암묵적 커밋](https://dev.mysql.com/doc/refman/8.0/en/implicit-commit.html) 때문에 두 CREATE TABLE을 하나의 ROLLBACK으로 되돌릴 수 없다. 도구는 오류 발생 시 중단하고 어떤 객체도 자동 삭제·변경하지 않는다.

| 점검 결과 | 후속 조치 |
| --- | --- |
| 기본 6개 테이블만 있고 정의·트리거 정상 | 사전 검사 후 `--test --apply` |
| allocations만 있고 정의가 맞으며 비어 있음 | 원인을 해결하고 검사 후 `--test --apply --resume` |
| 추가 2개 테이블이 모두 정상 | 재실행은 DDL 없이 완료 검증 |
| 추가 테이블의 정의 불일치, 역순 부분 구성, 부분 구성에 데이터 존재 | 자동 복구 중단. 객체·행을 보존하고 수동 검토 |
| 기본 테이블·트리거 불일치 또는 예상 밖 객체 | 적용 중단. 마이그레이션으로 기존 객체를 고치지 않음 |

```powershell
.\.venv\Scripts\python.exe -m database.migrate --test --apply --resume
```

`IF NOT EXISTS`, DROP, ALTER, TRUNCATE, seed 재적용을 통한 오류 숨김은 사용하지 않는다. 새 정의가 다른데 같은 이름만 있다고 성공으로 처리하지 않는다.
초기 v0.2-2 적용 당시 테이블 2개 생성 후 MySQL이 Generated Column에 붙인 괄호·문자셋 표기 때문에 검사기가 정의 불일치를 보고했다. 당시 SQL 의미가 같은 것을 확인하고 비교 정규화를 보완했다. 테이블은 재생성·변경하지 않았으며 이번 재검증에서도 정의가 일치했다.

## 기존 검사·복구 도구

- `inspect_schema.definitions()`는 기본값으로 v0.1 자산을 반환한다. `definitions("v0.2-2")`는 추가 SQL을 포함한 8개 정의를 반환한다.
- `inspect_schema.inspect()`는 실제 존재하는 추가 테이블로 버전을 감지한다. 6개는 v0.1, 8개는 v0.2-2, 한 개만 추가된 상태는 partial-v0.2-2다.
- `database.recover`는 완전한 6개·8개 구성의 기존 테이블을 보존한다. 부분 마이그레이션은 쓰기 전에 거부하고 `database.migrate`를 안내한다. 할당 테이블의 정의를 ALTER하거나 자동 생성하지 않는다.
- 기존 seed-if-empty는 인식한 전체 테이블의 행 수를 검사하므로 추가 테이블에 이력이 있어도 seed를 다시 입력하지 않는다. 이번에는 이 옵션을 사용하지 않았다.
- pytest는 정의가 맞는 6개·8개 DB를 인식한다. 신규 할당 테스트는 8개 테이블이 필요하며 마이그레이션을 자동 실행하지 않는다.

## 승인된 개발 DB 적용 결과 (2026-11-08)

적용 시각은 **2026-11-08 13:41:32 KST**다. 이번 명시적 승인 범위인 allocations / allocation_gpus 추가만 수행했다. 일반 CLI는 계속 `--test` 없는 `--apply`를 연결 전에 거부한다. 기존 보호 코드를 변경하거나 개발 쓰기 옵션을 추가하지 않았다.

1. `database.migrate`의 두 DB dry-run과 읽기 전용 지문을 확인했다. 개발 DB는 6개, 테스트 DB는 8개이며 모든 seed·트리거가 정상이었다.
2. 공식 **mysqldump 8.0.46**으로 개발 DB 구조·모든 데이터·트리거를 `--single-transaction` 방식으로 백업했다. 비밀번호는 명령 인자·파일·로그에 넣지 않았다.
3. 별도 디렉터리·포트의 임시 MySQL 8.0.46에 실제 복원해 모든 행·테이블 정의 6개·트리거 4개 지문을 원본과 비교했다. 기존 개발·테스트 DB에는 복원하지 않았다. 검증 인스턴스는 종료하고 임시 데이터 디렉터리만 정리했다.
4. 백업 해시·복원 증거·최신 원본 지문을 다시 확인한 뒤 Git 제외 경로의 일회성 실행기로 전용 개발 연결에 `migrate(connection, apply=True)`를 호출했다. 기존 함수의 advisory lock·사전 검사·CREATE 2개·사후 검증을 그대로 재사용했다.
5. 개발 DB 8개 테이블·트리거 4개, 신규 테이블 각 0행, 원본 6개 테이블의 원문 DDL 및 전체 데이터 지문 일치를 확인했다. 테스트 DB는 변경되지 않았다.
6. 개발 DB의 읽기 전용 기능과 전용 테스트 DB의 전체 pytest **240 passed**, MySQL 전용 **83 passed**를 확인했다. 테스트 후에도 기존 행·정의·트리거가 보존됐다.

로컬 백업은 `database/backups/v0.2-2-dev-20261108T133702+0900/development-before.sql`(14,017 bytes)이다. SHA-256은 `5df21db8b055fcb36e20be0d1ae5e73fc5c575f14654841fb42cc37a3e852aea`다.
같은 디렉터리에 백업 manifest, 사전·사후 지문, 실제 복원 검증 결과와 읽기 전용 기능 확인 결과를 보관한다. 이 디렉터리 전체는 Git 제외 대상이며 덤프 내용은 문서·로그·GitHub에 올리지 않는다.

이번에는 DROP/TRUNCATE/기존 테이블 ALTER/seed 재입력/기존 트리거 재생성 및 기존 MySQL 전역 설정 변경을 수행하지 않았다. 두 CREATE TABLE이 실패하면 암묵적 커밋으로 일부 객체가 남을 수 있어 자동 삭제·재시도 없이 중단하도록 실행했다. 실제 적용에는 부분 실패가 없었다.
002는 두 DB 모두 재적용할 작업이 없다. 개발 DB에 테스트 할당을 생성하지 않았다. 배정·반환·중복 방지·동시성 검증은 계속 전용 테스트 DB에서 수행한다.

## 003: v0.3-1 대안·고객 응답

[003_workload_proposals.sql](003_workload_proposals.sql)은 job_proposals / proposal_responses만 생성한다. 기존 8개 테이블·4개 트리거·seed·할당 이력을 변경하지 않는다. 이번 사용자 범위는 **테스트 DB 적용만**이며 개발 DB 적용은 별도 승인 전까지 금지다.

```powershell
# 읽기 전용 사전 검사: 개발 / 테스트
.\.venv\Scripts\python.exe -m database.proposal_migrate
.\.venv\Scripts\python.exe -m database.proposal_migrate --test
# 아직 003이 없는 테스트 DB에만 적용. 현재 테스트 DB에는 재실행 불필요
.\.venv\Scripts\python.exe -m database.proposal_migrate --test --apply
```

검사기는 완전한 6개·8개·10개 구성과 partial-v0.3-1을 구분한다. 003은 완전한 v0.2-2 구성과 정확한 원본 트리거를 요구한다. 알 수 없는 테이블·뷰·트리거·루틴·이벤트, 기존 정의 불일치는 거부한다. 이름만 같다고 성공 처리하지 않는다.
002와 같은 DB별 advisory lock으로 지원 마이그레이션의 동시 실행을 막고 기존 8개 테이블의 모든 행·정의 및 트리거 지문을 전후 비교한다. 코드의 `base_fingerprint(connection, "v0.2-2")`가 이 8개를 비교하며 기본 인자는 기존 6개 의미를 유지한다.

| 상태 | 처리 |
| --- | --- |
| 정상 기존 8개 테이블 | 사전 검사 후 테스트 DB에만 CREATE 2개 |
| job_proposals만 있고 정의가 맞으며 비어 있음 | 원인 해결·검사 후 명시적 `--test --apply --resume` |
| 두 신규 테이블 모두 정상 | 사전 검사에서 pending 없음; DDL 재실행 불필요 |
| 역순 부분 구성·부분 구성에 데이터·정의 불일치·예상 밖 객체 | 기존 객체·행을 보존하고 중단 |

DDL은 암묵적으로 커밋되므로 오류 시 생성된 첫 테이블을 자동 삭제하지 않는다. DROP/ALTER/TRUNCATE, 기존 트리거 재생성, seed 재입력으로 복구하지 않는다. 구형 recover도 partial-v0.3-1은 쓰기 전에 거부하고 새 도구를 안내한다. 개발 DB `--apply`는 연결 전 차단한다.

2026-11-08 양쪽 8개 구성과 모든 seed·기존 지문을 확인한 후 테스트 DB의 공식 mysqldump 백업을 Git 제외 경로 `database/backups/v0.3-1-test-20261108T140211+0900/test-before.sql`에 생성했다. 두 신규 테이블을 추가한 뒤 10개 정의가 일치하고 기존 8개·트리거가 보존됨을 확인했다. 개발 DB는 여전히 8개다.
전체 364개 및 실제 MySQL 134개 검증 후 신규 테이블은 각 0행이고 기존 seed·할당 이력·트리거 지문이 같다. 백업과 감사 파일은 업로드하지 않는다. 상세 결과는 [검증 기록](../../docs/v0.3-1-validation.md)을 따른다.

## 004: v0.3-2 수락 대안의 실제 배정

004_allocation_bases.sql은 allocation_bases 한 개만 생성한다. 기존 schema.sql·002·003을 재실행하지 않는다. 개발 DB에는 003·004를 적용하지 않는다.

```powershell
# 읽기 전용 점검; 실제 적용 전 전체 데이터·정의·트리거 백업
.\.venv\Scripts\python.exe -m database.basis_migrate --test
# 아직 004가 없는 완전한 003 테스트 DB의 최초 적용
.\.venv\Scripts\python.exe -m database.basis_migrate --test --apply
```

CLI의 개발 --apply는 연결 전에 차단하며 함수도 대상 이름이 _test가 아니면 쓰기를 거부한다. 기존 10개 정의·4개 트리거, 예상 밖 객체/루틴/이벤트, 전체 행·정의 지문을 검사한다. 002/003과 같은 DB별 advisory lock을 사용한다. 새 테이블이 이미 정확히 존재하면 DDL 없이 검증한다. 불일치·오류는 자동 삭제·ALTER·재생성 없이 중단한다. DDL은 암묵적으로 커밋되므로 사후 검사 실패 시 객체를 보존한다. 원본 10개 모든 값·정의·트리거를 전후 비교하고 백필하지 않는다.

노트북 적용 전 공식 mysqldump 8.0.46으로 테스트 DB 10개·트리거 4개·모든 데이터를 --single-transaction으로 저장했다. 백업·감사 파일은 Git 제외 경로 database/backups/v0.3-2-laptop-20261110/에만 둔다. 실제 결과는 [v0.3-2 검증](../../docs/v0.3-2-validation.md)을 따른다.
