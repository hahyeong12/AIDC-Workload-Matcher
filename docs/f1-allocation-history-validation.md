# F1 수정 검증 — 배정 이력이 있는 Job의 대안·수락 제한

기준 커밋: `cd8c752` (v0.4-2 + 문서). 작업 브랜치: `fix/f1-no-proposals-after-allocation`.
작업일: 2026-11-18 (Asia/Seoul), 노트북 전용 venv(Python 3.14.6).
범위: GPU 중심 v0.4-2 서비스·화면의 동의 무결성 보강. **DB 스키마·제약·마이그레이션·기존 데이터는 변경하지 않는다.**

## 1. 발견 내용 (독립 심사 F1)

`ProposalService.generate()`와 `respond()`는 해당 Job의 기존 할당을 확인하지 않았다. 할당 중(ACTIVE)이면
자기 GPU가 점유로 계산되어 원본이 "부족"으로 보이므로, 이미 실행 중인 Job에 축소 대안이 생성·수락될 수 있었다.
완료/실패 후에도 다른 Job이 자원을 쓰면 같은 일이 생겼다. 더 흔한 경로는 **자원 부족 때 생성한 옛 대안을,
이후 원본으로 배정된 뒤 수락하는 것**이었다.

자원 무결성은 깨지지 않았다(이후 `allocate_proposal`이 "다른 배정 근거" 충돌로 거부). 그러나
실행될 수 없는 수락이 삭제 불가능한 이력으로 남고, Job당 하나뿐인 수락 자리(`uq_proposal_responses_accepted_job`)를
영구 점유했다. 기존 테스트는 항상 **다른 Job**을 배정한 뒤 대상 Job으로 대안을 만들었기 때문에 이 경로를 포착하지 못했다.

## 2. 확정 정책

| 상황 | 결과 |
| --- | --- |
| 배정 이력(ACTIVE·COMPLETED·FAILED 중 하나라도) 있는 Job의 대안 생성 | `ProposalConflict`, 웹 409, 쓰기 없음 |
| 같은 Job의 **새** ACCEPTED 응답 | `ProposalConflict`, 웹 409, 쓰기 없음 |
| 같은 Job의 기존 제안에 대한 새 REJECTED 응답 | 허용(고객 의사 기록) |
| 이미 기록된 응답의 같은 의사 재요청 | 기존 응답 ID·시각을 그대로 반환(멱등). 수락 → 배정 → 같은 수락 재요청 포함 |
| 배정 이력이 없는 Job | 기존 대안 생성·수락·거절 동작 그대로 |

새 예외 클래스·DB 객체는 추가하지 않았다. `RETURNED`라는 할당 상태는 없다. 반환은 `allocation_gpus.released_at`이며
할당 상태는 ACTIVE / COMPLETED / FAILED 세 가지다.

## 3. 구현

- `AllocationRepository.has_allocation_history(job_id)`: `SELECT 1 FROM allocations WHERE job_request_id = %s LIMIT 1`.
  **비잠금 읽기**다.
- `ProposalService.require_no_allocation_history()`를 `generate()`(장비 잠금 직후, 제안 INSERT 전)와
  `respond()`(기존 응답 재사용 분기 뒤, 새 ACCEPTED INSERT 직전)에서 호출한다.
- 화면: Job 상세의 "대안 생성" 버튼은 이력이 있으면 비활성화, 제안 화면의 "이 대안 수락" 버튼은 비활성화하되
  "거절"은 유지한다. 안내 문구를 함께 표시한다. 화면 판단은 표시용이며 실제 판단은 잠금 안의 서비스가 한다.

### 동시성과 잠금 순서

두 서비스 모두 기존과 같이 **모델 → Job → 요구조건** 행을 `FOR UPDATE`로 먼저 잠근다. 할당을 쓰는 모든 경로
(`allocate`, `allocate_proposal`)도 같은 잠금을 먼저 잡는다. 따라서

- 경쟁하는 배정이 먼저 커밋되면, Job 잠금을 기다린 뒤 실행되는 READ COMMITTED 읽기가 그 할당을 본다.
- 이 트랜잭션이 끝날 때까지 같은 Job의 새 할당은 생길 수 없다.

할당 확인을 잠금 읽기(`latest_for_job`, `FOR UPDATE`)로 하면 `generate()`에서 Proposal보다 할당을 먼저 잠그게 되어
문서화된 전역 순서(… → GPU → Proposal → 응답 → 할당)와 어긋난다. 비잠금 읽기는 Job 잠금이 이미 직렬화를 보장하므로
정확성은 같고 잠금 순서에 새 단계를 추가하지 않는다.

## 4. 테스트

### 신규 (재현 → 수정 후 통과)

| 파일 | 테스트 | 종류 |
| --- | --- | --- |
| `tests/test_proposal_service.py` | 이력 있는 Job의 생성 거부·잠금 순서 / 이력 없는 생성 유지 / 새 수락 거부·잠금 순서 / 새 거절 허용 / 수락·거절 재사용(2) | 단위 6 |
| `tests/test_web.py` | 상태 3종의 대안 생성 버튼 비활성화 / 수락 비활성화·거절 유지 | 단위 4 |
| `tests/test_mysql_proposals.py` | ACTIVE·COMPLETED·FAILED에서 생성 거부와 지문 불변(3) / 옛 대안 수락 거부·거절 허용·거절 재사용(3) | MySQL, SAVEPOINT 롤백 |
| `tests/test_mysql_accepted_allocations.py` | 수락 → 배정 → 같은 수락 재요청 재사용, 이후 생성 거부, 다른 제안 거절 허용 | MySQL, SAVEPOINT 롤백 |
| `tests/test_mysql_web.py` | 웹에서 배정 후 생성·수락 409, 지문 불변, 안내 표시, 거절 303 | MySQL, SAVEPOINT 롤백 |
| `tests/test_mysql_accepted_concurrency.py` | 실제 두 연결: allocate가 잠금 보유 중 respond 대기 → 배정 커밋 후 수락 거부 | MySQL, 실제 COMMIT + 정확한 ID 정리 |

수정 전 코드에서 신규 단위 테스트 중 동작을 검증하는 7개가 실패함을 확인했다(재사용·거절 허용 같은 보존 테스트는 수정 전후 모두 통과).
`test_mysql_accepted_concurrency.py`의 `live` 픽스처에는 제안 ID를 추적하는 `generate` 헬퍼만 추가했다. 정리 방식은 같다.

### 실행 결과

| 구분 | 명령 | 결과 |
| --- | --- | --- |
| 비-MySQL 전체 | `python -B -m pytest -q -m "not mysql" -p no:cacheprovider --basetemp <저장소 밖 새 경로>` | **392 passed, 226 deselected** (기존 382 + 신규 10) |
| 수집 | 같은 옵션 + `--collect-only` | 618 collected (MySQL 226 = 기존 217 + 신규 9) |
| 기능 커밋 단독 | `48fc723`을 임시 worktree에서 비-MySQL 실행 | 382 passed (기존 테스트 회귀 없음) |
| 전체 + MySQL (1차) | `python -B -m pytest -q -rfEs -p no:cacheprovider --basetemp <저장소 밖 새 경로> --run-mysql` | **617 passed, 1 failed, 0 skipped** / 57.4초. MySQL 226개 중 225 passed |
| 실패 1건 재실행 | 같은 테스트 1개, `--basetemp` 미지정(프로젝트 기본 `.pytest-tmp/`) | **1 passed** |

## 4-1. MySQL 통합 실행 기록 (2026-11-18, 사용자 승인 범위)

- 실행기: `%LOCALAPPDATA%\venvs\AIDC-Workload-Matcher-laptop\Scripts\python.exe` (Python 3.14.6), `PYTHONDONTWRITEBYTECODE=1`.
- 쓰기 대상: `TEST_MYSQL_*` → localhost `aidc_workload_matcher_test` 하나. 개발 DB는 기존 읽기 전용 웹 테스트 1건만 접속했다. 시연 DB에는 테스트가 접속하지 않았다(아래 감사만 읽기 전용 접속).
- 시작 전: Git 작업 트리 깨끗함(`b585b58`). 테스트 DB는 seed만 있고 할당·제안·응답·근거 0행. 세 DB 기준선이 직전 측정과 일치했다.
- **실패 1건 분석**: `test_mysql_proposal_concurrency.py::test_real_cli_generation_details_acceptance_rejection_and_history`. DB 관련 단언(실제 CLI 생성·수락·거절, 원본·할당 지문 불변)을 모두 통과한 뒤 마지막 증거 JSON을 상대 경로 `.pytest-tmp/`에 쓰다가 `FileNotFoundError`가 났다. `.pytest-tmp`는 `--basetemp`를 지정하지 않을 때만 conftest가 만들기 때문이며, 저장소에 파일을 남기지 않으려고 저장소 밖 basetemp를 지정한 실행 방식에서 드러난 **기존 테스트의 숨은 경로 의존**이다. F1과 무관하고, 픽스처 정리는 정상 수행됐다. 같은 테스트를 기본 방식으로 다시 실행해 통과했다. 테스트 코드는 이번 범위에서 수정하지 않았다.
- F1 동시성 테스트(`test_acceptance_waiting_behind_original_allocation_is_refused_after_commit`, 0.85초): 실제 두 연결에서 `allocate(2)`가 모델·Job 잠금을 보유한 동안 `respond(2, ACCEPTED)`가 잠금 획득을 시도하고 **0.2초 동안 획득하지 못함(대기)**을 확인했다. 배정 커밋 뒤 잠금을 얻은 `respond`는 `ProposalConflict("…배정 이력…")`로 거부됐고, `proposal_responses` 0행, Job 2 할당 1행이었다. 커밋된 할당 2건(Job 1·2)과 제안 1건은 정확한 ID로 정리됐다.

### 세 DB 전후 비교 (읽기 전용 감사: 행 수·데이터·스키마·트리거 지문)

| DB | 데이터 | 스키마 | 보호 트리거 4개 | AUTO_INCREMENT |
| --- | --- | --- | --- | --- |
| 테스트 `aidc_workload_matcher_test` | 동일 | 동일 | 동일 | 증가(아래) |
| 개발 `aidc_workload_matcher` | 동일 | 동일 | 동일 | 변화 없음 |
| 시연 `aidc_workload_matcher_demo` | 동일 | 동일 | 동일 | 변화 없음 |

테스트 DB AUTO_INCREMENT(다음 값, 실행 전 → 1차 실행 + 재실행 후): allocations 540 → 623 (+83), customers 1164 → 1332 (+168),
gpu_models 1172 → 1341 (+169), gpus 2672 → 3053 (+381), job_proposals 983 → 1113 (+130, 재실행 +3 포함),
job_requests 1566 → 1806 (+240), proposal_responses 461 → 527 (+66, 재실행 +3 포함), servers 1522 → 1741 (+219).
InnoDB는 롤백·삭제된 행의 번호를 회수하지 않으므로 생기는 **번호 소비이며 행 데이터 변화가 아니다**. 모든 테이블의 행 수와 데이터 지문은 실행 전과 같다.

## 5. 정책 문서화 (F2: Job당 수락 1회)

`uq_proposal_responses_accepted_job`과 서비스 검사로 Job당 수락은 한 번이며 철회·변경 API가 없다. 수락한 대안의 배정이
자원 부족으로 실패하면 전체가 롤백되어 할당이 남지 않으므로 다음 중 하나를 택한다.

1. 같은 수락 대안으로 나중에 다시 배정을 시도한다(재검증 후 성공 가능).
2. 원본 조건으로 배정한다(원본이 가용해진 경우).
3. 다른 조건이 필요하면 **새 Job을 등록**하고 새 대안·동의를 받는다. 옛 Job과 새 Job의 연결은 DB에 기록되지 않는다(한계).

같은 Job 안에서 다른 대안을 다시 수락하는 "재협의"는 지원하지 않는다. 이를 지원하려면 수락 철회 이력과 UNIQUE 의미를 바꾸는
스키마 변경이 필요하며 이번 범위가 아니다.

## 6. 범위 밖과 남은 문제

- F3(배정 근거 복합 FK), F4(시각 타입), F5(계정 권한 분리), M0-B, 개발·시연 DB 변경은 포함하지 않았다.
- 배정 이력 검사는 서비스 규칙이다. 직접 SQL로 `proposal_responses`에 INSERT하는 경로는 DB가 막지 않는다(F5 권한 분리와 연관).
- 위 CLI 테스트의 `.pytest-tmp/` 상대 경로 의존은 별도 수정 후보다(예: `tmp_path` 사용). 이번 커밋에서는 바꾸지 않았다.
- 웹 화면 버튼 비활성화는 조회 시점의 표시다. 실제 거부는 서비스가 잠금 안에서 다시 판단한다.
