# 테스트 v0.1

저장소 루트에서 설치된 가상환경으로 실행한다.

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

기본 실행은 환경 설정, DB 분리 정책, 지원 버전, 비밀정보 비노출 및 초기화 안전장치를 검증한다. MySQL에 접속하지 않는다.
통합 테스트는 수집되지만 `--run-mysql` 옵션이 없으면 명시적으로 skip한다. 이 결과는 MySQL 제약 검증 성공을 의미하지 않는다.

## 실제 MySQL 통합 테스트

1. 개발 DB와 다른 전용 테스트 DB 및 계정을 발급받는다. 이름은 `_test`로 끝나야 한다.
2. `.env`에 `TEST_MYSQL_HOST/PORT/DATABASE/USER/PASSWORD`를 모두 설정한다. 개발용 값으로 대체하지 않는다.
3. 빈 테스트 DB에 [DB 안내](../database/README.md)에 따라 schema와 seed를 한 번 적용한다.
4. 연결 확인 후 테스트를 실행한다.

```powershell
.\.venv\Scripts\python.exe -m app.db --test
.\.venv\Scripts\python.exe -m pytest -q --run-mysql
```

명시적으로 통합 테스트를 켠 경우 설정·연결·스키마 문제는 skip하지 않고 실패로 처리한다.
검증 대상은 6개 PK, 5개 FK, UNIQUE, GPU 수량·배치 플래그·상태 제약, 서버/GPU 및 요청/요구조건 JOIN, 원본 수정·삭제 거부다.
SQLite나 모의 객체 결과를 MySQL 무결성 검증으로 취급하지 않는다.

각 테스트는 UUID가 포함된 자기 데이터만 INSERT하고 끝나면 전체 ROLLBACK한다. 원본 UPDATE/DELETE 거부 테스트도 해당 테스트가 만든 행에만 시도한다. 기존 행을 수정하거나 삭제하지 않는다.
테스트는 DB나 테이블을 생성·삭제·초기화하지 않으며 COMMIT하지 않는다. 반복 실행해도 테스트 행은 남지 않지만 InnoDB 자동 증가 번호에는 간격이 생길 수 있다.
향후 배정·대안 구현 후 수락 시 재검증, 동시 할당, 전체 롤백과 자원 반환 테스트를 추가한다.

## 이번 환경의 검증 기록 (2026-11-03)

- Windows / Python 3.14.6 / pytest 9.2.0에서 기본 실행 결과: **20 passed, 27 skipped**.
- 27개는 실제 MySQL이 필요한 통합 테스트이며 통과로 계산하지 않는다. `.env`와 MYSQL_*/TEST_MYSQL_* 접속 환경변수가 없어 실행하지 않았다.
- 개발·테스트 연결 확인 CLI는 각각 필요한 변수 누락을 보고하고 종료 코드 1로 종료했다. 서버 연결 시도, schema·seed 실행, 테이블 생성은 수행하지 않았다.
- Python 컴파일, 의존성 검사(`pip check`), Git 공백 검사, 로컬 문서 링크, SQL 파일 구성과 seed PK/FK 참조 관계의 정적 검사는 통과했다.
- 정적 검사는 MySQL 서버의 SQL 문법·CHECK·트리거 실행 검증을 대체하지 않는다. 실제 PK/FK/UNIQUE/CHECK 및 원본 보존 검증은 테스트 DB 준비 후 `--run-mysql`로 수행해야 한다.
