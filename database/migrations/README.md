# v0.2-2 추가 마이그레이션

[002_transactional_allocations.sql](002_transactional_allocations.sql)은 allocations / allocation_gpus만 생성한다. 기존 데이터가 있는 DB에 schema.sql을 다시 실행하지 않는다.
현재 개발 DB는 v0.1의 6개 테이블, 테스트 DB는 v0.2-2의 8개 테이블이다. 개발 DB 적용은 **별도 사용자 승인 전까지 미수행**이다.
2026-11-08 두 DB를 다시 조회해 테이블·트리거 정의와 모든 seed 값이 일치함을 확인했다. 이번에는 두 DB 모두 사전 검사만 실행했고 마이그레이션을 적용하거나 재실행하지 않았다. 테스트 DB의 `pending_tables`는 비어 있으며 개발 DB에는 추가 2개 테이블이 남아 있다. [실제 검증 기록](../../docs/v0.2-2-validation.md)을 참고한다.

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

## 개발 DB에 남은 작업

별도 사용자 승인을 받은 뒤 점검 시간에 개발 DB 연결로 같은 `migrate(connection, apply=True)` 사전 검사·추가 DDL·사후 검증을 수행해야 한다. CLI는 `--test` 없는 `--apply`를 연결 전에 거부하므로 이번 작업 중 개발 DB에 적용할 수 없다.
원본 6개 테이블·4개 트리거·seed를 유지하고 추가 2개 테이블만 생성한다. 승인 전에는 하드웨어 CLI만 사용할 수 있으며, 할당 CLI는 마이그레이션된 테스트 DB에서 `--test`로 검증한다.
