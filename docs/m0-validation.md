# M0 시연 검증 — M0-B 실행 계획

> 작성일: 2026-11-18 (Asia/Seoul). **절차 설계만 완료, 실제 실행 전이다.** 이 문서는 DB 쓰기·복원 대상 생성·Flask 쓰기 활성화의 승인이 아니다. 이번 문서 작업에서는 제품 코드와 DB를 변경하지 않았다.

기기별 실측, M0-A 구성·seed·백업의 파일명·해시 및 미검증 사항은 [환경 인계 기록](ENVIRONMENT_HANDOFF.md)에 모아 관리한다. M0의 전체 완료 조건은 [마스터 플랜](PROJECT_MASTER_PLAN.md#5-버전별-개발-계획-각-단계-승인-후-진행)을 따른다. 노트북 M0-A는 완료됐지만 **백업 실제 복원 검증과 M0-B 영구 COMMIT 시연은 미완료**다.

## 범위와 기존 구현

노트북 `localhost:3306/aidc_workload_matcher_demo`에서 기존 GPU-only 서비스를 사용한다. 신규 Job 원문·요구조건을 영구 저장하고, 고객 동의·무예약·배정 직전 재검증·GPU 반환·이력이 다른 연결에서도 보이는지 확인한다. CPU/RAM·실제 GPU 실행·실인증·성능 측정은 포함하지 않는다.

| 기능 | 기존 구현·검토 근거 | 추가 작업 |
| --- | --- | --- |
| 원문·GPU 요구조건의 원자적 등록 | [JobService.register](../app/services/jobs.py), [등록 통합 테스트](../tests/test_mysql_jobs.py)의 `test_registration_exact_parent_child_detail_and_matching`, `test_second_insert_actual_fk_failure_rolls_back_first_insert` | 제품 기능 추가 불필요. 기존 신규 원본 테스트의 롤백 검증과 별도로 실제 COMMIT·독립 조회 필요 |
| 같은 모델의 분산·수량 축소 대안 | [ProposalService.generate / generate_drafts](../app/services/proposals.py), [단위 테스트](../tests/test_proposal_service.py)의 `test_job003_distribution_and_reduction_are_distinct_and_explain_risks` | 추가 제품 구현 불필요 |
| 동의 검사·무예약·자원 재검증 | [AllocationService.basis_for / allocate_proposal](../app/services/allocations.py), [통합 테스트](../tests/test_mysql_accepted_allocations.py)의 `test_missing_or_rejected_consent_cannot_allocate`, `test_resources_are_rechecked_after_acceptance_no_reservation` | 추가 제품 구현 불필요. 수락은 예약·실행 보장이 아님 |
| 원자적 반환·근거·이력 | [AllocationService.finish](../app/services/allocations.py), [AllocationRepository](../app/repositories/allocations.py), 같은 통합 테스트의 `test_seed_alternative_actual_gpus_basis_return_and_history` | 추가 제품 구현 불필요 |
| 시연 DB 고정·실제 COMMIT 관찰·중단·재개 기록 | 기존 CLI의 연결은 [app.db](../app/db.py)의 MYSQL_* 또는 TEST_MYSQL_* 선택 방식. CLI에 시연 DB 전용 대상 강제 옵션은 없음 | **후속 승인 후** Git 밖 운영 래퍼 작성·검토 필요. 현재 M0-A 운영 도구의 구성·seed·백업·읽기 점검을 M0-B 실행기로 간주하지 않음 |
| 별도 DB에 백업 복원·트리거 효력 확인 | 백업 파일 동일성만 확인된 상태 | 복원 대상·권한·검증·종료 처리를 정하고 별도 승인 필요 |

위 테스트는 **코드 검토 근거이며 이번 문서 작업에서 실행하지 않았다.** 특히 [실제 경합 테스트](../tests/test_mysql_accepted_concurrency.py)와 롤백 어댑터의 성공은 이 시연의 신규 원본 영구 저장 실측을 대신하지 않는다.

## 실행 전 승인과 보호 조건

1. **격리 복원 검증을 먼저 승인받는다.** 정확한 새 복원 DB 이름·생성/복원 계정·권한·원본 보호 거부 시험·검증 후 보존/정리 범위를 명시한다. 복원 DB는 현재 미생성이며 개발·테스트·시연 DB 어느 것에도 덮어쓰지 않는다. 백업 SHA-256 일치는 파일 동일성만 보여 주므로, SQL 복원 가능성·문자셋·테이블/키/트리거 재현·DEFINER 및 실행 권한을 증명할 수 없다. 트리거가 있더라도 실행 권한 문제로 원본 보호가 정상 동작하지 않을 수 있어 독립 복원 검증이 선행돼야 한다.
2. 승인된 격리 대상에만 복원하고 11개 테이블·4개 보호 트리거·키·정의·seed·원문 및 읽기 기능을 대조한다. 원본 UPDATE/DELETE가 의도한 `SQLSTATE 45000`으로 차단되는지 확인하는 시험도 이 승인 범위에서만 수행한다. 실패하면 원본 시연 DB로 진행하지 않으며, 계정·트리거·덤프를 자동 변경하지 않는다. 기존 데스크톱 테스트 DB 복원 보고는 이 검증을 대체하지 않는다.
3. 복원 검증 성공 후 **노트북 시연 DB에 신규 원본 2건과 아래 이력을 영구 보존하는 실행을 별도 승인받는다.** 이 문서의 설계 승인만으로 시작하지 않는다. 커밋된 원문은 삭제할 수 없고 실패 후에도 앞 단계의 COMMIT은 남는다는 점을 승인 범위에 포함한다.
4. 같은 기기의 단일 운영 프로세스로 실행하고 다른 CLI·웹 쓰기·DDL 작업을 멈춘다. 기존 `.env`·TEST_MYSQL_*·계정 권한은 변경하지 않는다. 전용 앱 계정의 시연 DB 범위 권한을 재확인하고 마이그레이션 계정으로 시연하지 않는다. Flask 쓰기 게이트는 계속 비활성화한다.
5. 후속 래퍼는 지정된 노트북 venv를 사용하고, Git 밖 `demo-app.env`를 **그 프로세스에만** 주입한다. 시연 앱 계정과 개발·테스트 계정이 다르고, 호스트가 로컬·포트가 3306·대상이 정확히 `aidc_workload_matcher_demo`인지 검사한다. 기존 CLI의 기본 MYSQL_*는 개발 DB를 가리킬 수 있으므로, 대상 검증 없는 CLI 쓰기 명령이나 `--test`를 사용하지 않는다. `WEB_ENABLE_WRITES=1`도 필요 없다.
6. 실행 직전 세 DB의 데이터·논리 정의·트리거 지문과 시연 DB 상태를 읽기 전용으로 기록한다. H100의 가용량이 서버별 3개·2개이고 기존 배정·대안·응답·근거가 비어 있는지 확인한다. 승인된 초기 상태와 다르면 중단한다. 기준 백업과 실행 직전 상태가 달라졌다면 별도 백업 범위부터 다시 정한다.
7. 래퍼는 **각 서비스 쓰기 전에 그 writer 연결의 `SELECT DATABASE()`와 접속 계정 일치를 확인하고 조회 트랜잭션을 rollback으로 닫는다.** `connection.in_transaction == False`를 확인한 전용 실제 연결을 서비스에 전달한다. 서비스가 BEGIN·COMMIT/ROLLBACK을 소유하며 `RollbackEnvelope`, SAVEPOINT 기반 가짜 commit, pytest fixture를 사용하지 않는다. 서비스 호출·커밋 결과·ID를 즉시 로컬 실행 기록에 남긴다.
8. 모든 확인은 writer와 다른 **새 observer 연결**에서 `REPEATABLE READ / consistent_snapshot / readonly`로 수행하고 종료 시 rollback·close한다. 매 단계 새 스냅샷을 사용해 이전 조회가 후속 COMMIT을 가리지 않게 한다. 독립 연결 확인에 성공하기 전에는 다음 쓰기로 진행하지 않는다. 비밀값·실계정명·백업 본문은 출력하거나 Git에 넣지 않는다.

## 시나리오와 실제 실행 순서

고정된 ID를 새로 할당한다고 가정하지 않는다. 아래 seed 참조 값은 실행 직전 원본 값과 대조한다. Job/Proposal/Response/Allocation ID는 **각 호출의 반환값**을 기록해 사용하고 `6`, `7` 같은 예상 AUTO_INCREMENT 값을 쓰지 않는다.

| 별칭 | 신규 등록 입력 또는 의미 |
| --- | --- |
| `A` | 고객 2, 모델 1=`H100-SXM-80GB`, 4개, `same_server_required=True`. 제목 `M0-B <run-id> 주 요청`, 원문 `H100-SXM-80GB 4개를 반드시 동일 서버에서 사용하고 싶습니다.` |
| `B` | 고객 1, 같은 모델 1, 2개, `same_server_required=True`. 제목 `M0-B <run-id> 경쟁 요청`, 원문 `H100-SXM-80GB 2개를 반드시 동일 서버에서 사용하고 싶습니다.` |
| `P` | A에 생성된 대안 중 `proposal_type=DISTRIBUTED`, 수량 4, 동일 서버 조건 False인 **유일한 항목의 proposal_id**. 리스트 첫 항목이라는 가정으로 선택하지 않음 |
| `R` | 고객 2의 P에 대한 ACCEPTED 응답. 반환 `CustomerResponse.response_id`를 기록한 값 |
| `L_B`, `L_A` | B 원본 배정과 A 수락 대안 배정의 반환 `AllocationResult.allocation_id`를 기록한 값 |

`run-id`는 실행 승인 때 정한 고유 로컬 기록 식별자다. 기존 seed Job 001~005를 변경하거나 배정하지 않는다. H100 서버 1에는 정상 GPU 1·2·3, 서버 2에는 정상 GPU 5·6이 있어 가용량은 **3+2=5**다. GPU 4는 FAULTY여서 제외된다. 아래 서비스 호출 표기는 **운영 래퍼 설계를 위한 API 순서이며 바로 실행할 스크립트가 아니다.**

| 단계 | 호출 순서 | 예상 DB 변경·트랜잭션 | 다른 연결의 기대 결과 |
| --- | --- | --- | --- |
| 0 | 실행 전 보호 조건 확인 | 읽기만 수행 | 승인된 기준선·원본·가용 H100 5개, 점유 0 |
| 1 | `JobService(writer).register(2, title_A, original_A, 1, 4, True)` → A | `job_requests` +1, `job_requirements` +1, 한 트랜잭션 COMMIT | A의 소유자·제목·원문·모델·수량·배치 조건이 입력과 정확히 일치. 두 행 모두 존재 |
| 2 | `JobService(writer).register(1, title_B, original_B, 1, 2, True)` → B | 같은 두 테이블 각각 +1, COMMIT | B의 두 행과 입력 일치. 신규 원본 등록만으로 점유는 생기지 않음 |
| 3 | A의 하드웨어/가용성 읽기 후 `ProposalService(writer).generate(A)` | 원본은 `HARDWARE_INSUFFICIENT` / `INSUFFICIENT_RESOURCES`. `job_proposals` +2, 한 COMMIT | P=4개 분산, 다른 대안=3개 동일 서버. `changes`·이유·주의사항이 원본과 일치. 응답·배정·GPU 점유는 여전히 0 |
| 4 | 동의 전에 `AllocationService(writer).allocate_proposal(A, P, 2)` | **의도한 실패** `AllocationConflict` / `ALLOCATION_CONFLICT`, 전체 ROLLBACK | 응답·할당·GPU 연결·근거 추가 없음. 원본 및 기존 두 대안 불변 |
| 5 | `ProposalService(writer).respond(A, P, 2, 'ACCEPTED').response_id` → R | `proposal_responses` +1, COMMIT | R의 Job·Proposal·고객·decision 일치. **할당·GPU 연결·근거는 여전히 0**, H100 가용 5. 수락은 무예약 |
| 6 | `AllocationService(writer).allocate(B).allocation_id` → L_B | `allocations` +1 ACTIVE, `allocation_gpus` +2, `allocation_bases` +1 ORIGINAL, 한 COMMIT | B의 원본 2개 동일 서버 배정. 현재 선택 규칙에서 서버 1 GPU 1·2. 나머지는 서버 1의 1개+서버 2의 2개=3개 |
| 7 | `AllocationService(writer).allocate_proposal(A, P, 2)` | **의도한 실패** `InsufficientResources` / `INSUFFICIENT_RESOURCES`, 전체 ROLLBACK | 수락 후에도 현재 가용 3<4이므로 A 배정은 없음. 단계 6 직후와 전체 행·정의 지문 일치. B의 ACTIVE 배정 1개·점유 2개만 유지 |
| 8 | `AllocationService(writer).finish(L_B)` | B의 헤더 1행 COMPLETED·ended_at 설정, GPU 연결 2행 released_at 설정, 한 COMMIT | 반환 시각이 헤더와 자식에 동일, 활성 점유 0·가용 5. B의 과거 배정·근거·연결은 남음 |
| 9 | 같은 P와 R로 `AllocationService(writer).allocate_proposal(A, P, 2).allocation_id` → L_A | `allocations` +1 ACTIVE, `allocation_gpus` +4, `allocation_bases` +1 ACCEPTED_PROPOSAL, 한 COMMIT | 실제 GPU 4개·두 서버 분산, 현재 선택 규칙은 GPU 1·2·3·5=3+1. 근거가 정확한 A·P·R·고객 2 및 수량 4·분산 허용을 참조. A의 원본은 여전히 동일 서버 True |
| 10 | `AllocationService(writer).finish(L_A)` | A의 헤더 1행 COMPLETED·ended_at 설정, GPU 연결 4행 released_at 설정, 한 COMMIT | 활성 점유 0·가용 5, 두 Job의 모든 이력·동의·적용 조건·반환 시각 보존 |
| 11 | 독립 조회로 원본·대안·응답·두 배정 상세·Job별/GPU별 이력 및 세 DB 지문 감사 | 읽기만 수행 | 아래 최종 체크리스트 만족. 개발·테스트 DB 지문과 시연의 기존 seed·장비·정의·트리거는 실행 전과 동일 |

현재 서비스는 가용 GPU를 `(server_id, gpu_id)` 오름차순으로 정렬하며 동일 서버 배정은 조건을 만족하는 첫 서버를 사용한다. 이 순서나 장비 배치가 달라지면 위 GPU ID를 억지로 맞추지 않고 실행을 중단해 계획을 검토한다. 하드웨어 매칭 CLI는 점유를 고려하지 않으므로 **단계 7의 부족 판단에는 `get_availability`와 실제 배정 서비스를 사용**한다.

동의 차단은 **변경된 대안 조건의 배정**에 대한 검사다. 원본 조건을 만족하는 B의 `allocate(B)`에는 Proposal 동의가 필요하지 않다. A의 원본 4개 동일 서버 조건을 임의로 수정해 분산 배정하지 않는다. 고객 ID는 업무 관계 검사 입력이며 실인증이 아니다.

단계 7은 제안 때의 조회 결과를 재사용하지 않고, 실제 배정 트랜잭션에서 모델→Job/요구조건→서버/GPU 순으로 잠근 뒤 점유를 다시 읽는 동작을 검증한다. 전체 시나리오는 **순차 COMMIT으로 자원 변동을 재현**하며 병렬 락 경합 실측은 아니다.

## 독립 연결 검증과 예상 영구 변경량

writer의 성공 반환만으로 통과 처리하지 않는다. observer가 정확한 시연 DB인지 확인하고 새 읽기 스냅샷에서 다음을 검사한다.

- 등록: 반환된 A·B ID로 `JobService.detail` 또는 매개변수 SELECT를 실행해 부모·자식 1:1, 원문 공백·문자열·조건을 확인한다. 이후에도 두 원본의 불변 컬럼이 최초 입력과 같아야 한다. seed 원본 001~005는 전체 행 지문으로 보존을 확인한다.
- 동의: `ProposalService.list_for_job/detail/history`로 A·P·R·고객 관계와 두 대안의 변경 필드·이유·주의사항을 확인한다. R의 존재가 점유를 만들지 않았는지 배정 관련 **세 테이블 전체 지문**과 가용량으로 확인한다.
- 실패: 단계 4·7 전후 모든 행·논리 정의·트리거 지문이 일치하고 A의 배정·근거가 0행이어야 한다. 실패는 예상한 코드와 rollback 완료를 둘 다 확인한다. 연결/권한 오류를 자원 부족으로 처리하지 않는다.
- 성공 배정: `AllocationService.detail`과 `history_for_job`으로 헤더·GPU 정확한 수량·서버·근거를 검증한다. `history_for_gpu`로 해당 GPU의 과거 반환·재사용 이력을 확인한다. ACTIVE 헤더 또는 미반환 GPU 연결이 점유로 취급되는 기존 정책을 따라 불일치를 숨기지 않는다.
- 반환: 각 배정의 `ended_at`과 모든 자식 `released_at`이 같고 상태가 COMPLETED이며, ACTIVE 헤더 0·미반환 연결 0·H100 가용 5개여야 한다. 반환은 GPU 장비 행의 하드웨어 상태를 바꾸는 동작이 아니다.
- 보호 범위: 개발·테스트 DB의 전체 행·논리 정의·트리거 지문은 그대로다. 시연 DB는 아래 승인된 변화만 허용하고 고객·모델·서버·GPU·기존 원본·키·트리거를 보존한다. AUTO_INCREMENT 소비·번호 공백은 행 오염이나 롤백 실패로 판단하지 않는다.

| 테이블 | 최종 변화량 | seed만 있는 초기 상태 기준 최종 행 수 |
| --- | --- | --- |
| `job_requests` / `job_requirements` | 각각 +2, A·B 원본 영구 보존 | 각각 7 |
| `job_proposals` | +2, A의 분산·수량 축소 대안 보존 | 2 |
| `proposal_responses` | +1, P에 대한 ACCEPTED 보존. 수량 축소 대안에는 응답하지 않음 | 1 |
| `allocations` | +2, 둘 다 COMPLETED | 2 |
| `allocation_gpus` | +6, 모두 반환 시각 존재 | 6 |
| `allocation_bases` | +2, ORIGINAL 1·ACCEPTED_PROPOSAL 1 | 2 |
| `customers` / `gpu_models` / `servers` / `gpus` | 변경 없음 | 3 / 2 / 4 / 10 |

성공한 서비스 트랜잭션은 8회, 의도한 실패·rollback은 2회다. 수락 후 자원이 부족했던 P와 R도 그대로 보존한다. 단계 7의 부족은 GPU를 INSERT하기 전 `choose_gpus`에서 판정되므로, 이 시연만으로 **두 번째 GPU INSERT 도중 오류 같은 중간 쓰기 실패까지 실측했다고 주장하지 않는다.** 그 보장은 `allocation_transaction`과 기존 통합 테스트의 `test_injected_failures_roll_back_every_write`, 실제 트랜잭션 테스트의 `test_actual_transaction_rolls_back_gpu_and_header_on_basis_failure`에서 별도로 다룬다. 실패 주입·병렬 경합·직접 SQL 변조 시험은 이 M0-B 실행 범위에 추가하지 않는다.

## 실패 시 중단과 재실행

- 대상·계정·스키마·기준 지문·백업/복원 검증이 불일치하거나, 의도한 두 실패가 성공해 버리거나, 예상 밖 오류·행 수·부분 연결·점유·독립 조회 불일치가 나오면 **즉시 후속 쓰기를 중단**한다. 중단 지점·확인된 ID·커밋 여부·안전한 오류 코드와 독립 조회 결과를 Git 밖에 남긴다.
- COMMIT 응답이 유실되거나 성공 여부가 불확실하면 자동 재호출하지 않는다. observer에서 반환 ID 또는 run-id 제목의 원문·요구조건·응답·배정 이력을 확인해 결과를 확정한다. `JobService.register`에는 요청 멱등성 키가 없어 반복 등록하면 영구 원본이 더 생긴다.
- 실패한 현재 트랜잭션의 rollback은 과거 단계의 COMMIT을 취소하지 않는다. 이미 생긴 A·B·대안·응답·배정 이력은 보존한다. seed 재입력·TRUNCATE/DELETE·트리거 해제·DB 초기화·기존 DB로 백업 덮어쓰기를 복구 수단으로 자동 실행하지 않는다.
- 중단 시 ACTIVE 배정이 있더라도 자동 정리하지 않는다. 대상 allocation_id·현재 상태를 확인한 뒤 `finish`로 반환할 범위를 별도로 승인받는다. 정상 시나리오의 단계 8·10 반환과 예외 중단 후 조치를 구분한다.
- 같은 근거 배정과 같은 종료 상태의 반복은 기존 결과를 재생할 수 있지만, 종료된 Job을 다시 실행하는 기능은 없다. 재시작 위치와 기존 ID를 검토한 뒤 승인받는다. 처음부터 반복하려면 신규 원본이 추가되는 별도 실행 범위가 필요하다.

## DB 없는 계획 계산의 재현

2026-11-18 다음 Python 계산을 노트북 전용 venv의 `python.exe -B -`에 표준입력으로 전달해 성공했다. 저장소 루트에서 실행하며 `seed.sql`을 메모리로 읽고 기존 순수 함수를 호출한다. DB 연결·서비스 쓰기 호출·pytest 실행은 없다. 대안 조건·선택 GPU·부족 판정의 교차검증이며 실제 COMMIT·잠금·rollback 검증으로 해석하지 않는다.

```python
import ast, re
from pathlib import Path
from dataclasses import replace
from app.matching import GPU, JobRequirement
from app.services.allocations import evaluate_availability, choose_gpus
from app.services.proposals import generate_drafts
from app.allocation import InsufficientResources
seed = Path('database/seed.sql').read_text(encoding='utf-8')
def rows(table):
    body = re.search(r'INSERT INTO ' + table + r' \([^;]*?\) VALUES\s*(.*?);', seed, re.S).group(1)
    return ast.literal_eval('[' + body + ']')
servers = {r[0]: r[1:] for r in rows('servers')}
inventory = [GPU(i, tag, model, status, server, *servers[server])
             for i, tag, model, server, status in rows('gpus')]
a = JobRequirement(1001, 1, 'NVIDIA', 'H100-SXM-80GB', 4, True)
b = replace(a, job_request_id=1002, gpu_count=2)
available = evaluate_availability(a, inventory, ())
assert (available.available_gpu_count, available.max_gpus_per_server) == (5, 3)
drafts = generate_drafts(available)
assert {(p.proposal_type, p.gpu_count, p.same_server_required) for p in drafts} == {
    ('DISTRIBUTED', 4, False), ('REDUCED_QUANTITY', 3, True)}
applied = replace(a, same_server_required=False)
assert [g.gpu_id for g in choose_gpus(evaluate_availability(b, inventory, ()))] == [1, 2]
busy = evaluate_availability(applied, inventory, (1, 2))
assert busy.available_gpu_count == 3
try:
    choose_gpus(busy)
except InsufficientResources:
    pass
else:
    raise AssertionError('Expected insufficient resources')
assert [g.gpu_id for g in choose_gpus(evaluate_availability(applied, inventory, ()))] == [1, 2, 3, 5]
assert a.same_server_required is True
print('M0-B offline plan check passed')
```

1001·1002는 메모리 계산용 가상 ID이며 DB에서 발급된 ID가 아니다.

## 실행 완료 기록의 조건

위 단계별 COMMIT·독립 조회 증거, 실제 ID·최종 증가량, 보호 대상 DB의 전후 지문, 백업 복원 검증 결과를 로컬 운영 기록에 남기고 공개 가능한 요약만 이 문서에 추후 추가한다. **현재는 계획이며 성공 결과·신규 Job ID·복원 검증 결과가 없다.** M0-B를 완료로 바꾸려면 위 실측이 모두 있어야 한다. 제품 기능 추가가 필요해지면 이 문서 작업에서 임의 구현하지 않고 범위를 다시 승인받는다.
