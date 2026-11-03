# 애플리케이션

v0.1은 `db.py`에 환경변수 기반 MySQL 연결과 읽기 전용 연결 확인 CLI를 제공합니다.
Flask 앱, 라우트, 웹 UI와 매칭·대안·배정 기능은 아직 구현하지 않았습니다.

저장소 루트에서 `.\.venv\Scripts\python.exe -m app.db`로 연결을 확인합니다.
`connect_database()`가 반환하는 연결의 트랜잭션·종료 책임은 호출자에게 있습니다.
세부 준비 방법은 [DB 안내](../database/README.md)를 확인합니다.

구현 시 요청 처리, 매칭·대안·배정 서비스, 데이터 접근을 분리합니다.
HTML 템플릿과 CSS/JavaScript 자산은 각각 `templates/`, `static/` 아래에 구성할 예정입니다.
배정 판단은 서버에서 수행하고, 실제 AI 학습 대신 실행 상태를 시뮬레이션합니다.

계층별 책임과 트랜잭션 경계는 [아키텍처](../docs/architecture.md)를 따릅니다.
