# 기기 전환 환경 동등화 루틴 (Environment Handoff)

> 이 문서는 절차와 마지막으로 확인된 기기별 상태를 기록한다. OneDrive/GitHub는 MySQL 서버의 내부 데이터를 동기화하지 않는다. 이 기록은 실시간 상태가 아니며 DB 수정 승인도 아니다.

## 시작 신호와 기본 원칙

사용자가 **“노트북이야”, “데스크톱이야”, “시작 루틴”**이라고 말하면 먼저 해당 컴퓨터의 개발 진도를 확인한다. 목표는 독립된 MySQL을 쓰면서도 **동일 Git 커밋·논리 스키마·기준 seed·재현 가능한 기능과 테스트** 상태를 유지하는 것이다. DB 계정 이름/설치 경로/AUTO_INCREMENT 값/개별 테스트 이력까지 같아야 하는 것은 아니다.

## 절차 (기본은 읽기 전용)

1. **기기·경로** — 노트북/데스크톱, **OneDrive 밖 `%USERPROFILE%\Desktop\AIDC-Workload-Matcher`**가 실제 저장소인지 확인한다. 기존 OneDrive 복사본을 실수로 열지 않는다. 경로가 이동했다면 기기별 실제 경로를 다시 보고한다.
2. **Git** — 현재 branch, HEAD, origin/main, 미커밋 변경·충돌 파일·잠금을 확인한다. HEAD가 같고 작업 트리가 깨끗하면 다시 Pull하지 않는다. 안전한 Fast-forward가 필요한 경우에만 Pull하며 강제 Reset/Rebase/Push 금지.
3. **Python** — 노트북 `%LOCALAPPDATA%\venvs\AIDC-Workload-Matcher-laptop\Scripts\python.exe`, 데스크톱 `%LOCALAPPDATA%\venvs\AIDC-Workload-Matcher-desktop\Scripts\python.exe`를 우선 확인한다. 모두 OneDrive 밖의 기기별 환경이다. 직접 절대 경로로 실행하고 버전·`pip check`·requirements 호환을 확인한다. 일반 `python`은 Anaconda `(base)`일 수 있다. 옛 OneDrive `.venv`는 사용·삭제·수정하지 않는다.
4. **MySQL 대상** — MYSQL_*와 TEST_MYSQL_*의 실제 SELECT DATABASE(), localhost 여부, 버전·스키마 버전·테이블/트리거·PK/FK/UNIQUE/CHECK·인덱스·정의 지문·현재 seed 값/행 수·필요 권한을 읽기 전용으로 검사한다. 비밀번호·개인 계정명은 출력하지 않는다.
5. **비교** — 아래 마지막 검증 기록과 현재 기기 실측을 비교하고, '현재 실측/다른 기기 과거 기록/미확인'을 구분한다. 다른 기기 DB에 현재 접속하지 않았다면 두 컴퓨터가 완전히 같은 상태라고 단정하지 않는다.
6. **차이 해소** — 필요한 마이그레이션·가상환경·설정 변경의 최소 경로를 먼저 제시한다. 어떤 컴퓨터의 어떤 DB에 무엇을 실행할지와 기존 데이터 보존·백업/복원 검증을 제시하고, 그 **구체적인 작업만 승인**받아 수행한다. 이 기기의 승인은 다른 기기에 자동 적용되지 않는다.
7. **재검증** — 각 DB의 기존 행·논리 정의·트리거 보존, 신규 테이블/제약, 읽기 조회, 승인된 테스트를 확인한다. MySQL을 변경하는 테스트도 DB 쓰기이므로 승인 범위에 포함시킨다. pytest 9의 `--basetemp`를 저장소 밖으로 지정할 때는 상위 폴더를 먼저 생성한다.
8. **보고·다음 단계** — 기기별 현재 상태, 차이, 미검증 사항, 성공·실패·복구 필요 여부를 보고하고서야 신규 기능 개발이나 다음 마일스톤으로 넘어간다.

## DB 변경 안전장치

- 데이터 변경, DDL, 복원, 시연 영구 COMMIT은 승인된 대상에만 수행한다. 003·004 테스트 전용 CLI를 개발 DB에 편법 적용하지 않는다.
- MySQL DDL은 암묵적으로 COMMIT된다. 실패 시 부분 상태를 보존하고 중단하며 자동 삭제·재생성·seed 재입력 금지.
- 개발/테스트 DB의 기준 seed와 원본 보호 트리거는 보존한다. 시연용 영구 Job은 자동 회귀 테스트 DB에 남기지 않는다.
- 신규 기능마다 코드 커밋과 해당 DB에 적용된 마이그레이션을 **기기별로 별도 확인**한다.

## 마지막 검증 기록 — 노트북 2026-11-18 / 데스크톱 2026-11-11 (실시간 아님)

노트북 값은 2026-11-18 동일 세션의 읽기 전용 시작 점검 실측이다. 문서 갱신 단계에서는 DB에 다시 접속하거나 테스트를 재실행하지 않았다. 데스크톱에는 오늘 접속하지 않았으므로 마지막 보고와 현재 동일하다고 단정하지 않는다.

| 항목 | 노트북 — 2026-11-18 직접 실측 | 데스크톱 — 2026-11-11 Codex 작업 결과·사용자 보고 |
| --- | --- | --- |
| Git | 시작 점검 `main=5fd42aa` / 원격 `bd52160`(문서 2개 차이), 깨끗한 트리. 승인 후 `git pull --ff-only origin main` 성공, 동기화 직후 HEAD·origin/main=`bd52160` | 004 작업 후 `5fd42aa`·깨끗한 작업 트리로 보고 |
| 프로젝트 경로 | `%USERPROFILE%\Desktop\AIDC-Workload-Matcher` (노트북) | `%USERPROFILE%\Desktop\AIDC-Workload-Matcher` (데스크톱, 사용자 이동 확인; 기기에서 재확인 필요) |
| MySQL | 8.0.46 / `localhost:3306` | 8.0.46 / `localhost:3306` |
| 개발 DB `aidc_workload_matcher` | v0.2-2 / **8개 테이블·보호 트리거 4개** | v0.2-2 / **8개·4개** |
| 테스트 DB `aidc_workload_matcher_test` | v0.3-2 / **11개·4개** | v0.3-2 / **11개·4개**, 004 적용·백업·독립 복원 검증 완료로 보고 |
| 시연 DB `aidc_workload_matcher_demo` | v0.3-2 / **11개·4개**, M0-A 구성·seed 입력 완료 | 구성 미확인 |
| 기준 데이터 | 세 DB 모두 seed 명시값 일치: 고객 3·모델 2·서버 4·GPU 10·원본 Job/요구조건 각 5. 할당·GPU 점유 및 존재하는 대안·응답·근거 테이블은 0행 | 기존 seed·원본 보존 및 테스트 후 임시 할당·대안 정리로 보고 |
| Python | 3.14.6 / 노트북 전용 venv 정상, Flask 3.1.3·Connector 26.10.0·pytest 9.2.0·dotenv 1.2.4 | 3.14.6 / 데스크톱 전용 venv 정상, 동일 고정 직접 의존성 설치 |
| 테스트 | 시작 점검: 비-MySQL 382 passed, 217 deselected. **F1 브랜치(`fix/f1-no-proposals-after-allocation`) 승인 실행: 전체 618개 중 617 passed·1 failed·0 skipped, 실패 1건(증거 파일 상대 경로 의존, DB 단언은 통과)은 기본 방식 재실행에서 passed. MySQL 226개 포함.** 실행 후 세 DB 데이터·스키마·트리거 지문 불변, 테스트 DB AUTO_INCREMENT만 증가([F1 검증 기록](f1-allocation-history-validation.md)) | **2026-11-11 전체 599 passed**, MySQL 217 포함(보고) |
| 시연 백업·M0 진행 | 백업·manifest 존재, 크기·SHA-256 일치. 실제 복원 검증 미실시, M0-B 영구 COMMIT 시연 미완료 | 시연 DB·시연 백업 미확인(테스트 DB 복원 보고와 구분) |

노트북 Python: `%LOCALAPPDATA%\venvs\AIDC-Workload-Matcher-laptop\Scripts\python.exe`.

데스크톱 Python: `%LOCALAPPDATA%\venvs\AIDC-Workload-Matcher-desktop\Scripts\python.exe`.

**노트북 시작 점검 근거:** 지정된 전용 venv의 requirements 고정 버전·`pip check`·주요 import 정상. `.env`의 MYSQL_*·TEST_MYSQL_* 필수 키가 모두 존재하고 해당 프로세스 환경변수 덮어쓰기는 없었다. `SELECT DATABASE()`·서버 버전 확인 후 읽기 전용 트랜잭션에서 `SHOW CREATE TABLE`·트리거 정의·PK/FK/UNIQUE/CHECK·인덱스·seed와 지문을 비교했다. 세 DB의 데이터·논리 정의·트리거가 2026-11-12 로컬 감사 기록 및 오늘 검사 전후 모두 일치했고 예상 밖 객체는 없었다.

비-MySQL 검증은 전용 venv에서 `pytest.main(['-q', '-m', 'not mysql', '-p', 'no:cacheprovider', '--basetemp', <새 시스템 Temp 하위 경로>])`로 실행했다. 매칭 CLI는 세 DB에서 seed Job 001~005 총 15건 정상(001·002·004 가능, 003·005 부족), Flask 테스트 클라이언트 GET은 대시보드·고객별 화면·등록 폼·Job 상세 총 30건 HTTP 200이었다. 웹 쓰기는 비활성화했으며 실제 브라우저·영구 COMMIT·MySQL 통합 테스트 결과로 해석하지 않는다.

**M0-A 시연 백업:** 운영 자료는 Git 밖 `%LOCALAPPDATA%\AIDC-Workload-Matcher-ops\m0a-demo-20261112`에 있다. 002→003→004 적용 기록과 seed 완료 감사 기록을 확인했다. `backups/demo-initial-20261112T151647+0900.sql`은 24,878바이트이며 manifest의 크기·테이블 11개·트리거 4개 목록과 일치했다. SHA-256은 `768b52f147761fccf136d855682a0b8c25659a0e6639e5f02cc1f7e7db59feaa`로 manifest와 일치했다. **해시는 파일 동일성만 검증하며 실제 복원 가능성을 증명하지 않는다. 격리된 복원 대상 생성·실제 복원 검증은 아직 미실시다.** 백업 본문·환경 파일·접속 비밀·개인 계정명은 Git에 넣지 않는다.

시연 앱·마이그레이션 계정은 개발·테스트 계정 및 서로와 구분되며 로컬 연결과 시연 DB 범위 권한을 확인했다. 이 계정들은 읽기 전용 계정이 아니므로 점검에서는 읽기 전용 트랜잭션만 사용했다. `.env`는 Git 제외 상태다. 2026-11-12에 보고됐던 `credential-manager-core` 경고가 오늘 Pull에서도 재현됐으나 동기화는 성공했다. 인증 설정은 수정하지 않았고 Push 인증은 미검증이다.

**완료와 남은 승인:** 노트북 M0-A 구성·seed·백업 해시 확인은 완료됐지만 **M0 전체 및 M0-B 실제 영구 COMMIT 시연은 미완료**다. 다음 DB 작업은 격리 백업 복원 검증과 노트북 시연 DB의 단계별 영구 시연을 각각 승인받아 진행한다. 개발 DB 003·004 적용, 데스크톱 시연 DB 구성, 벤치마크 DB 생성도 별도 승인 대상이다. 데스크톱 테스트 DB의 004·백업·격리 복원·599개 통과는 2026-11-11 보고이며 노트북 시연 백업의 복원 검증으로 대체할 수 없다.

## 시작 루틴 결과 보고 양식

| 항목 | 이 기기의 실측 | 다른 기기의 마지막 확인·날짜 | 상태 |
| --- | --- | --- | --- |
| Git HEAD/작업 트리 | ... | ... | 일치/차이/미확인 |
| Python/venv/패키지 | ... | ... | 정상/복구 필요 |
| 개발 DB 구조·기준 데이터 | ... | ... | 일치/차이/미확인 |
| 테스트 DB 구조·기준 데이터 | ... | ... | 일치/차이/미확인 |
| 테스트 결과 | ... | ... | 최신/과거 기록/미실행 |

최종 판단: 이번 기기에서 새 기능을 시작해도 되는지, 차이가 있다면 무엇부터 맞추어야 하는지, 추가 승인 범위를 명확히 말한다.
