# v0.1 부분 스키마 복구

현재는 관리자가 두 DB의 트리거를 생성한 뒤 테스트 DB seed 입력과 전체 검증까지 완료한 상태다. 최신 결과는 이 문서 마지막의 **v0.1 검증 완료**를 따른다. 아래 오류·중단 기록은 복구 전 이력이다.

## 초기 점검 결과 (관리자 복구 전)

2026-11-03에 프로젝트의 `.env`를 통해 개발 DB와 테스트 DB에 접속하여 읽기 전용으로 확인했다. 접속 계정·호스트·비밀번호는 문서와 로그에 기록하지 않는다.

| 항목 | 개발 DB | 테스트 DB |
| --- | --- | --- |
| MySQL | 8.0.46 | 8.0.46 |
| 기존 테이블 | 6개, 모두 InnoDB | 0개 |
| 원본 보존 트리거 | 0개 | 0개 |
| 기존 행 | 6개 테이블 모두 0개 | 테이블 없음 |
| schema.sql과 비교 | 6개 테이블 모두 일치 | 비교할 테이블 없음 |
| log_bin / log_bin_trust_function_creators | 1 / 0 | 1 / 0 |

개발 DB의 컬럼 순서·타입·기본값, PK·FK·UNIQUE·CHECK, 인덱스, 엔진·문자셋·collation을 `SHOW CREATE TABLE`과 비교했다. 서버의 인덱스 표기·CHECK 외곽 괄호·RESTRICT 순서·자동 증가 다음 번호 등 표현 차이만 정규화한다. 모든 SQL 문법에 대한 범용 동등성 판정기가 아니며 낯선 차이는 자동 변경 없이 중단한다.

## 확인한 오류와 중단 지점

누락된 첫 트리거 생성만 시도하여 다음 오류를 실제로 확인했다. 생성은 실패했고 DB 객체나 행은 추가되지 않았다.

```text
stage=create-trigger
object=trg_job_requests_no_update
operation=CREATE TRIGGER
errno=1419
SQLSTATE=HY000
```

이것은 기존 실행의 보존된 원본 로그를 읽은 결과가 아니라 동일한 첫 누락 객체에서 재현한 오류이다.
초기화 코드가 테이블 6개 → 트리거 4개 → seed 순서로 실행되므로, 현재 상태 및 재현 결과는 첫 트리거에서 멈춘 흐름과 일치한다. seed 단계에는 도달하지 못했고 실제 행 수도 모두 0이다.

Binary Logging이 켜지고 trust 설정이 0이면 일반 TRIGGER 권한 외에 추가 생성 권한 검사가 적용된다. 서버 버전에 따른 권한 정책은 [MySQL Stored Program Binary Logging](https://dev.mysql.com/doc/refman/8.0/en/stored-programs-logging.html)과 [CREATE TRIGGER](https://dev.mysql.com/doc/refman/8.0/en/create-trigger.html)를 따른다. 일반 앱 계정에 SUPER 권한을 부여하거나 전역 보안 설정을 완화하는 방법을 사용하지 않는다.
현재 계정의 쓰기 작업은 1419 확인 시점에 중단했다. 테스트 DB에 테이블을 만들어 같은 부분 실패를 반복하지 않았다.

## 도구와 보호 장치

- `python -m database.recover`: 기본 동작은 읽기 전용이다. 버전, 전역 설정 값 두 개, 고정 프로젝트 객체의 존재·정의 일치 여부·행 수만 출력한다. 실제 행 데이터나 DEFINER·접속 정보는 출력하지 않는다.
- `--test`: TEST_MYSQL_* 전용 연결을 사용한다. 기존 `_test` 이름 및 개발 DB와 다른 이름 검증을 유지한다.
- `--apply`: 테이블 6개가 SQL과 일치해야만 누락된 트리거를 생성한다. 이미 일치하는 트리거는 건너뛰고, 다른 정의나 예상 밖 테이블·트리거가 있으면 쓰기 전에 중단한다. 테이블을 재생성하거나 변경하지 않는다.
- `--seed-if-empty`: `--apply`와 함께만 사용한다. 트리거 4개를 재검증한 뒤 전체 테이블이 비어 있는 경우에만 seed를 한 트랜잭션으로 넣는다. SERIALIZABLE에서 빈 상태를 재확인하고 INSERT 오류 시 seed 전체를 롤백한다. 기존 행이 하나라도 있으면 seed를 거부한다. upsert나 중복 무시는 없다.
- `--export-admin-sql 파일`: 읽기 전용 점검 후 **그 시점에 누락된 트리거만** 지정한 SQL 파일로 내보낸다. 대상 DB를 명시하고 테이블·행·계정·전역 설정을 변경하는 SQL은 넣지 않는다. `database/` 내부 새 파일만 허용하며, 내용이 다른 기존 파일을 덮어쓰지 않는다.
- `database.initialize --confirm-empty`: 기존 빈 DB 검사 정책을 그대로 유지한다. 부분 스키마에 재실행해도 거부한다.
- `--tables-only`: 관리자가 트리거를 별도로 생성해야 할 때 **완전히 빈 DB**에 테이블만 준비한다. 트리거와 seed 미설치 상태임을 명시하고, 완료된 초기화라고 출력하지 않는다.
- 진단 출력은 단계, 고정 객체명, MySQL 오류 번호, SQLSTATE만 사용한다. Connector 오류 원문·전체 SQL·접속값은 출력하지 않는다.

DDL은 암묵적으로 커밋되므로 일부 트리거 생성 후 실패할 수도 있다. 다시 읽기 전용 점검한 다음 누락 항목만 이어서 처리한다. 도구 실행 동안 다른 세션에서 해당 DB의 DDL·초기 데이터 쓰기를 하지 않는다. INFORMATION_SCHEMA는 계정 권한에 따라 보이는 객체가 달라지므로 점검 계정에 해당 DB의 SELECT와 TRIGGER 권한이 있는지 관리자가 확인해야 한다.

## 사용자가 수행할 개발 DB 복구

저장소 루트에서 아래 명령을 실행한다. 예시는 기존 가상환경을 사용하며 비밀번호를 명령 인자로 전달하지 않는다.

1. 현재 상태를 다시 확인한다.

   ```powershell
   .\.venv\Scripts\python.exe -m database.recover
   ```

2. 관리자가 MySQL Workbench의 기존 관리자 연결에서 [admin_missing_triggers.sql](../database/admin_missing_triggers.sql)을 열어 검토·실행한다. 이 파일은 점검 시점에 누락된 개발 DB 트리거 4개만 포함한다. 대상 DB `aidc_workload_matcher`를 확인하고 오류 발생 시 실행 중단 옵션을 사용한다. 관리자 비밀번호를 `.env`나 코드에 옮기지 않는다. `schema.sql` 전체를 다시 실행하지 않는다.

   파일 생성 후 상태가 변했다면 최신 상태에서 새 파일명으로 다시 내보낸다. 기존 파일을 강제 덮어쓰지 않는다.

   ```powershell
   .\.venv\Scripts\python.exe -m database.recover --export-admin-sql database/admin_missing_triggers_reviewed.sql
   ```

   명시적 DEFINER는 넣지 않았으므로 생성한 Workbench 계정이 트리거 DEFINER가 된다. 관리자가 해당 계정의 유지·실행 권한 정책을 검토해야 한다. 앱 계정의 전역 권한 변경은 필요하지 않다. SQL 자체가 실패하면 오류를 확인하고 거기서 중단한다.

3. 관리자가 생성한 뒤 일반 개발 계정으로 재점검한다. 6개 테이블과 4개 트리거의 `matches`가 모두 true여야 한다. 모두 비어 있는 상태가 유지되었다면 seed를 적용한다.

   ```powershell
   .\.venv\Scripts\python.exe -m database.recover
   .\.venv\Scripts\python.exe -m database.recover --apply --seed-if-empty
   ```

   기대 행 수는 customers=3, gpu_models=2, servers=4, gpus=10, job_requests=5, job_requirements=5이다. 이미 행이 있다면 `--seed-if-empty`를 반복하지 말고 읽기 전용으로 내용을 별도 검토한다.

## 사용자가 수행할 테스트 DB 준비 및 통합 검증

현재 테스트 DB는 비어 있는 것으로 확인했지만 실행 직전에 다시 확인한다. 개발 DB를 테스트 대상으로 사용하지 않는다.

```powershell
.\.venv\Scripts\python.exe -m database.recover --test
.\.venv\Scripts\python.exe -m database.initialize --test --confirm-empty --tables-only
.\.venv\Scripts\python.exe -m database.recover --test --export-admin-sql database/admin_missing_triggers_test.sql
```

관리자가 Workbench에서 **새로 생성된 테스트 DB용 SQL 파일**의 대상 DB를 확인하고 실행한다. 개발 DB용 파일을 테스트 DB에 대신 실행하지 않는다. 기존 데이터나 객체가 발견되면 테이블 준비 명령은 중단하므로 삭제해서 맞추지 않는다.

```powershell
.\.venv\Scripts\python.exe -m database.recover --test
.\.venv\Scripts\python.exe -m database.recover --test --apply --seed-if-empty
.\.venv\Scripts\python.exe -m pytest -q --run-mysql
```

통합 테스트는 테스트 전용 행을 만들고 롤백한다. 원본 UPDATE/DELETE 거부 테스트도 새로 만든 행에만 시도하며 기존 행은 변경하지 않는다. 개발 DB에서 파괴적 확인을 하지 않는다.

## 이번 변경의 검증 범위

- 실제 MySQL 읽기 전용 조회와 1419 재현, 개발 DB용 관리자 SQL 내보내기를 수행했다.
- 기본 pytest: **53 passed, 27 skipped**. 진단 비노출, 부분 스키마 보호, 정의 차이 거부, 누락 트리거만 생성, 기존 데이터 seed 거부와 실패 시 롤백을 모의 연결로 검증했다.
- 실제 MySQL 통합 테스트 27개는 실행하지 않았다. 관리자 트리거 생성이 필요하고 테스트 DB가 비어 있기 때문이다. 모의 테스트를 실제 PK/FK/UNIQUE/CHECK·트리거 검증으로 보고하지 않는다.
- 개발 DB 복구, seed 저장, 테스트 DB 준비 및 실제 제약·원본 변경 거부 검증은 **관리자 작업 이후 남아 있다**. DB 복구가 완료되었다고 기록하지 않는다.

## v0.1 검증 완료 (2026-11-03)

관리자가 개발·테스트 DB의 트리거를 생성한 후 Codex가 다음 작업을 직접 실행했다. 위의 미완료 기록을 대체하는 최신 상태다.

- 읽기 전용 재점검: 양쪽 DB 모두 테이블 6개와 트리거 4개가 존재하며 `schema.sql` 정의와 일치한다.
- 개발 DB: 이미 저장되어 있던 가상 데이터는 조회만 했다. 기존 데이터, 테이블, 트리거에 쓰기 작업을 하지 않았다.
- 테스트 DB: 6개 테이블이 모두 비어 있음을 확인하고 `python -m database.recover --test --apply --seed-if-empty`로 기존 트리거를 보존하면서 seed INSERT만 커밋했다. DDL을 실행하지 않았다.
- 최종 행 수는 두 DB 모두 customers=3, gpu_models=2, servers=4, gpus=10, job_requests=5, job_requirements=5이다. 테스트 종료 후 seed에 지정된 모든 컬럼 값을 비교해 정확히 일치하며 테스트용 추가 행은 남지 않았음을 확인했다.
- `python -m pytest -q --run-mysql`: **82 passed**. 이 중 실제 MySQL 통합 테스트는 **27개 전부 통과**했고 skip·실패·오류는 없다. PK/FK/UNIQUE/CHECK, 소속 관계, 상태 구분 및 원본 UPDATE/DELETE 거부를 실제 MySQL 8.0.46에서 검증했다.
- 첫 전체 실행에서 FK 테스트 5개는 예상 오류 번호 1452 대신 1216을 받아 실패했다. 두 코드는 모두 부모 행이 없는 참조 무결성 위반이다. 테스트를 정확히 이 두 코드와 SQLSTATE 23000만 인정하고 거부된 행의 미저장도 확인하도록 수정했다. 권한이나 DB 제약을 완화하지 않았다. [MySQL 오류 정의](https://dev.mysql.com/doc/mysql-errors/8.0/en/server-error-reference.html), [제한 계정의 오류 메시지 차이](https://bugs.mysql.com/bug.php?id=99336)를 참고한다.
- 관리자 SQL 재실행으로 발생한 Error 1359는 중복 트리거 생성 시도에 대한 오류다. 이번에는 관리자 SQL을 재실행하지 않았다. `admin_missing_triggers_test.sql`은 복구 당시의 생성 기록으로 보관하며, 이미 설치된 DB에는 다시 실행하지 않는다.

### Windows 임시 경로 확인과 조치

기존 `tmp_path` 사용 테스트는 수정 전 단독 실행에서도 통과했다. 현재 실행 계정에서는 기본 시스템 임시 폴더의 pytest 상위 디렉터리를 읽을 수 있었고, 과거 PermissionError는 재현되지 않았다. 따라서 당시 ACL이나 어느 파일의 접근 거부였는지는 확정하지 않는다.
Windows 기본 실행은 이제 `tests/conftest.py`에서 저장소 내부 `.pytest-tmp/run-<무작위값>/tmp`를 선택한다. 실행마다 전용 상위 폴더를 새로 만들고 아직 존재하지 않는 자식 경로를 pytest에 전달한다. 기본 사용자 임시 폴더에 대한 의존을 제거하고, 이전 실행 폴더와 사용자 파일을 삭제하거나 권한을 변경하지 않는다. 명시적으로 지정한 `--basetemp`는 존중한다.
pytest는 기존 `--basetemp` 경로를 지우므로 고정 경로를 반복 사용하지 않도록 했다. 경로 선택과 기존 파일 보존을 회귀 테스트 2개로 확인했다. [pytest 임시 경로 문서](https://docs.pytest.org/en/stable/how-to/tmp_path.html)를 따른다. `.pytest-tmp/`는 Git에서 제외하며 이 도구가 과거 실행 폴더를 자동 정리하지 않는다.

현재 실행 기준 남은 오류나 필수 관리자 작업은 없다. 서버 전역 보안 설정은 변경하지 않았고 관리자 인증정보는 저장하지 않았다.
