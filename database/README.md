# 데이터베이스 v0.1

6개 InnoDB 테이블과 가상 seed를 제공한다. 관계와 컬럼 정의는 [ERD](erd.md)를 확인한다.

- [schema.sql](schema.sql): 6개 테이블과 원본 보존 트리거 4개. DB 생성·삭제·초기화 명령은 없다.
- [seed.sql](seed.sql): 가상 고객 3명, 모델 2개, 서버 4대, 개별 GPU 10개, 요청·요구조건 각 5건.
- [initialize.py](initialize.py): 명시적으로 선택한 비어 있는 DB에만 SQL을 적용하는 선택적 CLI.
- [inspect_schema.py](inspect_schema.py), [recover.py](recover.py): 읽기 전용 점검과 누락 트리거만 추가하는 복구 도구. 실제 오류 분석과 관리자 실행 절차는 [부분 스키마 복구 안내](../docs/mysql-recovery.md)를 따른다.

## 실행 전 조건

1. MySQL 8.0.16 이상과 별도 프로젝트 전용의 빈 DB를 준비한다. DB 생성과 계정 발급은 이 도구가 수행하지 않는다.
2. `.env.example`을 `.env`로 복사하고 발급받은 접속 정보를 채운다. 비밀번호를 터미널 인자나 Git에 기록하지 않는다.
3. 실행 위치는 저장소 루트이며 `requirements.txt`를 설치한 가상환경을 사용한다.
4. 초기화 계정에는 해당 프로젝트 DB의 CREATE, TRIGGER, SELECT, INSERT 등 필요한 권한만 부여한다. 일반 앱·테스트 계정에는 DDL 권한이 필요하지 않다. 서버 설정에 따라 트리거 생성 권한을 관리자가 추가 설정해야 할 수 있다.

## 연결만 확인하기

```powershell
.\.venv\Scripts\python.exe -m app.db
.\.venv\Scripts\python.exe -m app.db --test
```

첫 명령은 `MYSQL_*`, 두 번째는 `TEST_MYSQL_*`만 사용한다. `SELECT VERSION(), 1`만 실행하며 테이블이나 데이터를 생성하지 않는다.
환경변수 누락 시 접속을 시도하지 않고 종료한다. 프로세스 환경변수가 `.env`보다 우선한다. Connector 오류 원문은 비밀정보 노출 방지를 위해 출력하지 않는다.

## 선택적 최초 적용

**기존 데이터가 없는 새 프로젝트 DB에만** 아래 명령을 실행한다. 확인 옵션 외에도 도구가 DB의 테이블·뷰·루틴·트리거·이벤트 존재 여부를 검사하고 하나라도 있으면 쓰기 전에 중단한다.

```powershell
# Development database configured in MYSQL_*
.\.venv\Scripts\python.exe -m database.initialize --confirm-empty

# Separate integration-test database configured in TEST_MYSQL_*
.\.venv\Scripts\python.exe -m database.initialize --test --confirm-empty
```

테스트 DB 이름은 `_test`로 끝나야 하고 개발 DB 이름과 달라야 한다. 접속 정보는 개발용 설정에서 가져오지 않는다.
이 CLI는 프로젝트의 고정 SQL 파일만 분할 실행하며, 복합 트리거 본문이나 SQL 문자열 내부 세미콜론을 처리하는 범용 SQL 실행기가 아니다. SQL 형식 확장 시 분할 방식을 함께 변경한다.

같은 DB에 재실행하면 중단한다. `IF NOT EXISTS`, `INSERT IGNORE`, `REPLACE`, 기존 행을 갱신하는 upsert를 사용하지 않는다.
실패 시 단계·DB 객체명·MySQL 오류 번호·SQLSTATE를 출력한다. 테이블 생성 후 트리거에서 실패했다면 전체 초기화를 반복하지 말고 복구 도구로 점검한다. Binary Logging 권한 문제로 관리자가 트리거를 만들어야 하는 새 DB에는 `--tables-only`로 테이블만 준비할 수 있으며 트리거·seed는 설치되지 않는다.
seed INSERT는 하나의 트랜잭션에서 실행하며 실패하면 seed 전체를 롤백한다. `seed.sql`을 다른 도구로 실행한다면 호출자가 동일한 트랜잭션과 오류 시 롤백을 보장해야 한다.
스키마 DDL은 암묵적 커밋이 발생하므로 중간 오류 시 일부 테이블이 남을 수 있다. 자동 삭제나 재생성 없이 중단하므로 상태를 확인하고 별도 마이그레이션을 검토한다. 적용 중에는 다른 세션에서 같은 DB의 DDL을 수행하지 않는다.

스키마 및 가상 seed SQL은 Git에서 관리한다. 실제 데이터, 백업, 덤프는 각각 `data/`, `backups/`, `dumps/` 아래에 두며 Git에서 제외한다.
