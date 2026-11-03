# AIDC-Workload-Matcher

컴퓨터공학과 데이터베이스 텀프로젝트로 개발하는 AI 데이터센터(AIDC) 작업 매칭 및 자원 배정 시스템입니다.
고객의 AI 학습 작업 요청을 데이터센터의 실제 GPU 자원과 운영 조건에 비교하여 실행 가능성을 판단하고, 가능한 실행 계획과 대안을 제시합니다.

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

## 현재 구조

```text
AIDC-Workload-Matcher/
├── README.md
├── .gitattributes
├── .gitignore
├── .env.example
├── requirements.txt
├── docs/
│   ├── requirements.md
│   ├── architecture.md
│   └── decisions.md
├── database/
│   └── README.md
├── app/
│   └── README.md
└── tests/
    └── README.md
```

현재는 문서와 디렉터리만 구성한 초기 단계입니다. MySQL 테이블, DB 연결 코드, Flask 실행 진입점과 기능, 테스트 코드는 아직 구현하지 않았습니다.

## 개발 환경 준비

Python 3.11 이상을 기준으로 개발합니다. 아래는 Windows PowerShell 예시입니다.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

`.env`는 로컬에서만 값을 채우고 커밋하지 않습니다. 예시 파일에는 실제 접속 정보나 비밀값을 넣지 않습니다.
환경변수 로딩과 Flask 실행은 후속 구현 단계에서 연결합니다. 현재 설치 과정은 MySQL 테이블을 생성하지 않습니다.

## 개발 방향

1. 요구사항과 개념적 데이터 모델을 검토하고 ERD 및 무결성 제약을 설계합니다.
2. MySQL 스키마와 가상 초기 데이터를 설계합니다.
3. 자원 조회, 원본 요청 저장, 실행 가능성 판단을 구현합니다.
4. 대안 제시·수락 및 트랜잭션 기반 자원 배정을 구현합니다.
5. 시뮬레이션과 화면을 연결하고 동시 배정 및 조건 보존을 검증합니다.

상세 기능은 [요구사항](docs/requirements.md), 계층과 처리 흐름은 [아키텍처](docs/architecture.md), 설계 근거는 [설계 결정](docs/decisions.md)에 기록합니다.
