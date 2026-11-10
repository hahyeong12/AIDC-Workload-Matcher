"""Local-only Flask adapter. No schema initialization, migration or seed writes."""

from contextlib import contextmanager
import hmac
from ipaddress import ip_address
import os
import re
import secrets
from threading import Lock
from time import monotonic

from dotenv import load_dotenv
from flask import Flask, Response, abort, flash, g, redirect, render_template, request, session, url_for
from werkzeug.exceptions import HTTPException, SecurityError

from app.allocation import AllocationError
from app.db import PROJECT_ROOT, DatabaseConfigError, DatabaseOperationError, connect_database, database_step
from app.job import InvalidJobInput, parse_id, validate_registration
from app.matching import MatchingError, parse_job_id
from app.proposal import CustomerMismatch, ProposalError
from app.repositories.workspace import WorkspaceRepository
from app.services.allocations import AllocationService
from app.services.jobs import JobService
from app.services.proposals import ProposalService
from app.services.workspace import WorkspaceService
from database.inspect_schema import inspect, require_matching_tables


class RegistrationTickets:
    """Bounded, session-bound, one-use confirmation tickets for this one process."""

    def __init__(self):
        self.pending = {}
        self.lock = Lock()

    def issue(self, values, owner):
        with self.lock:
            now = monotonic()
            self.pending = {k: v for k, v in self.pending.items() if now - v[0] < 900}
            if len(self.pending) >= 256:
                abort(409, "등록 확인 대기 한도에 도달했습니다. 잠시 후 다시 시도하세요.")
            ticket = secrets.token_urlsafe(32)
            self.pending[ticket] = (now, owner, values)
            return ticket

    def take(self, ticket, owner):
        with self.lock:
            entry = self.pending.get(ticket)
            if entry is None or monotonic() - entry[0] >= 900 or not hmac.compare_digest(entry[1], owner):
                abort(409, "등록 확인이 만료됐거나 이미 제출됐습니다. 목록 확인 후 새로 작성하세요.")
            del self.pending[ticket]
            return entry[2]


def get_connection():
    if "connection" not in g:
        with database_step("web-connect"):
            factory = g.app_config.get("WEB_TEST_CONNECT")
            g.connection = factory() if g.app_config["TESTING"] and factory else connect_database("MYSQL_")
    return g.connection


@contextmanager
def read_snapshot():
    connection = get_connection()
    try:
        with database_step("web-read-begin"):
            connection.start_transaction(isolation_level="REPEATABLE READ", readonly=True, consistent_snapshot=True)
        yield connection
    finally:
        with database_step("web-read-end"):
            connection.rollback()


def schema_policy():
    if "policy" not in g:
        with read_snapshot() as connection:
            report = inspect(connection)
            require_matching_tables(report)
            if report["schema_version"] not in ("v0.2-2", "v0.3-1", "v0.3-2"):
                raise DatabaseConfigError("Web requires complete allocation tables")
            if not all(row["exists"] and row["matches"] for row in report["triggers"].values()):
                raise DatabaseConfigError("Original protection triggers are required")
            database = WorkspaceRepository(connection).database_name()
        config = g.app_config
        advanced = report["schema_version"] == "v0.3-2"
        test_database = (database.casefold().endswith("_test")
                         or database.casefold() == os.environ.get("TEST_MYSQL_DATABASE", "").casefold())
        # Only injected rollback adapters inside a test app can exercise test DB writes.
        rollback_test = (config["TESTING"] and config.get("WEB_TEST_ROLLBACK_ONLY") is True
                         and getattr(connection, "web_rollback_only", False) is True)
        target_matches = database == config["WEB_WRITE_DATABASE"]
        enabled = (config["WEB_ENABLE_WRITES"] is True and target_matches and advanced
                   and (not test_database or rollback_test))
        reason = ("웹 쓰기는 기본 비활성화입니다." if config["WEB_ENABLE_WRITES"] is not True else
                  "테스트 DB는 수동 웹 쓰기 대상으로 사용할 수 없습니다." if test_database and not rollback_test else
                  "명시한 쓰기 대상 DB와 실제 연결 DB가 다릅니다." if not target_matches else
                  "웹 쓰기는 정상 v0.3-2 스키마(11개 테이블)가 필요합니다." if not advanced else
                  "대상 DB와 스키마를 확인했습니다. 원본 등록은 영구 저장됩니다.")
        g.policy = dict(version=report["schema_version"], advanced=advanced, writable=enabled, reason=reason)
    return g.policy


def workspace():
    return WorkspaceService(get_connection(), advanced=schema_policy()["advanced"])


def selected_customer(customers):
    value = request.values.get("customer_id", session.get("customer_id"))
    if value is None:
        return customers[0]["customer_id"] if customers else None
    customer = parse_id(value, "customer_id")
    if not any(row["customer_id"] == customer for row in customers):
        from app.job import CustomerNotFound
        raise CustomerNotFound("존재하지 않는 고객입니다.")
    session["customer_id"] = customer
    return customer


def customer_from_form():
    return parse_id(request.form.get("customer_id"), "customer_id")


def require_write():
    if not schema_policy()["writable"]:
        abort(403, schema_policy()["reason"])


def registration_values():
    count = request.form.get("gpu_count", "")
    flag = request.form.get("same_server_required", "")
    if not re.fullmatch(r"[0-9]{1,10}", count) or flag not in ("0", "1"):
        raise InvalidJobInput("GPU 수량은 양의 정수, 동일 서버 여부는 0/1로 입력하세요.")
    return validate_registration(request.form.get("customer_id"), request.form.get("title"),
                                 request.form.get("original_request"), request.form.get("gpu_model_id"),
                                 int(count), int(flag))


def create_app(config=None):
    load_dotenv(PROJECT_ROOT / ".env", override=False)
    app = Flask(__name__)
    app.config.from_mapping(
        SECRET_KEY=os.environ.get("WEB_SECRET_KEY") or secrets.token_hex(32), DEBUG=False,
        WEB_ENABLE_WRITES=os.environ.get("WEB_ENABLE_WRITES") == "1",
        WEB_WRITE_DATABASE=os.environ.get("WEB_WRITE_DATABASE", ""),
        SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Strict",
        TRUSTED_HOSTS=["localhost", "127.0.0.1", "[::1]"],
        MAX_CONTENT_LENGTH=300_000, MAX_FORM_MEMORY_SIZE=280_000,
    )
    if config:
        app.config.update(config)
    # No debug traceback even when a caller supplies a configuration dictionary.
    app.config["DEBUG"] = False
    app.extensions["registration_tickets"] = RegistrationTickets()

    @app.before_request
    def protect_request():
        g.app_config = app.config
        try:
            local = ip_address(request.remote_addr or "").is_loopback
        except ValueError:
            local = False
        if not local:
            abort(403, "localhost에서만 사용할 수 있습니다.")
        if request.method == "POST":
            expected, actual = session.get("csrf"), request.form.get("csrf_token", "")
            if not expected or not hmac.compare_digest(expected.encode("utf-8"), actual.encode("utf-8")):
                abort(400, "요청 검증에 실패했습니다. 페이지를 새로고침한 뒤 다시 시도하세요.")

    @app.context_processor
    def template_context():
        if "csrf" not in session:
            session["csrf"] = secrets.token_urlsafe(32)
        return dict(csrf_token=session["csrf"], policy=g.get("policy"),
                    statuses={"REGISTERED": "등록 완료", "PROPOSED": "대안 검토", "ACCEPTED": "동의 기록",
                              "ACTIVE": "배정 중", "COMPLETED": "완료 · 반환", "FAILED": "실패 · 반환"})

    @app.after_request
    def security_headers(response):
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self'; "
            "frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.teardown_appcontext
    def close_connection(error):
        connection = g.pop("connection", None)
        if connection is not None:
            try:
                connection.rollback()
            except Exception:
                app.logger.warning("Web connection rollback failed; connection will be closed")
            finally:
                try:
                    connection.close()
                except Exception:
                    app.logger.warning("Web connection close failed")

    @app.errorhandler(Exception)
    def handle_error(error):
        if isinstance(error, SecurityError):
            # An untrusted Host has no URL adapter; never render links in that context.
            return Response("허용되지 않은 Host입니다.", status=400, content_type="text/plain; charset=utf-8")
        if isinstance(error, HTTPException):
            # Only our fixed descriptions are displayed; framework 500 never includes details.
            status = error.code or 500
            message = error.description if status in (400, 403, 409) else "요청한 페이지를 처리할 수 없습니다."
        elif isinstance(error, (MatchingError, ProposalError, AllocationError)):
            status = 403 if isinstance(error, CustomerMismatch) else 404 if error.code.endswith("NOT_FOUND") else 409
            if isinstance(error, InvalidJobInput) or error.code == "INVALID_JOB_ID":
                status = 400
            message = str(error)
        else:
            status, message = 503, "DB 연결·스키마 또는 처리 상태를 확인하세요. 변경 결과가 불확실하면 이력을 확인한 뒤 진행하세요."
            if isinstance(error, DatabaseOperationError):
                app.logger.warning("Web database operation failed: %s", error)
            else:
                app.logger.warning("Web request failed (%s)", type(error).__name__)
        return render_template("error.html", message=message, status=status), status

    @app.get("/")
    def dashboard():
        schema_policy()
        with read_snapshot():
            view = workspace()
            customers = view.catalog.customers()
            customer_id = selected_customer(customers)
            data = view.dashboard(customer_id)
        return render_template("dashboard.html", customers=customers, customer_id=customer_id, **data)

    def new_form(values=None, error=None, status=200):
        schema_policy()
        with read_snapshot():
            catalog = workspace().catalog
            customers, models = catalog.customers(), catalog.models()
            try:
                customer_id = selected_customer(customers)
            except MatchingError:
                if error is None:
                    raise
                customer_id = None
        return render_template("job_new.html", customers=customers, models=models, customer_id=customer_id,
                               values=values or {}, error=error), status

    @app.get("/jobs/new")
    def job_new():
        return new_form()

    @app.post("/jobs/preview")
    def job_preview():
        schema_policy()
        try:
            values = registration_values()
            with read_snapshot():
                jobs = JobService(get_connection())
                jobs.repository.require_customer(values[0])
                jobs.repository.require_model(values[3])
                models = workspace().catalog.models()
            model = next(item for item in models if item["gpu_model_id"] == values[3])
        except (MatchingError, StopIteration) as error:
            return new_form(request.form, str(error) if isinstance(error, MatchingError) else "GPU 모델을 확인하세요.", 400)
        ticket = app.extensions["registration_tickets"].issue(values, session["csrf"])
        return render_template("job_confirm.html", values=values, model=model, ticket=ticket, customer_id=values[0])

    @app.post("/jobs")
    def job_register():
        require_write()
        values = app.extensions["registration_tickets"].take(request.form.get("ticket", ""), session["csrf"])
        job_id = JobService(get_connection()).register(*values)
        flash("새 Job을 등록했습니다.", "success")
        return redirect(url_for("job_detail", job_id=job_id, customer_id=values[0]), code=303)

    @app.get("/jobs/<int:job_id>")
    def job_detail(job_id):
        job_id = parse_job_id(job_id)
        schema_policy()
        with read_snapshot():
            view = workspace()
            customer_id = selected_customer(view.catalog.customers())
            data = view.detail(job_id, customer_id)
        return render_template("job_detail.html", customer_id=customer_id, **data)

    @app.get("/jobs/<int:job_id>/proposals/<int:proposal_id>")
    def proposal_detail(job_id, proposal_id):
        if not schema_policy()["advanced"]:
            abort(409, "대안 검토는 정상 v0.3-2 스키마가 필요합니다. 현재는 조회 범위가 제한됩니다.")
        with read_snapshot():
            view = workspace()
            customer_id = selected_customer(view.catalog.customers())
            data = view.proposal(parse_job_id(job_id), parse_job_id(proposal_id), customer_id)
        return render_template("proposal.html", customer_id=customer_id, **data)

    @app.post("/jobs/<job_id>/actions/<action>")
    def job_action(job_id, action):
        require_write()
        job_id, customer_id = parse_job_id(job_id), customer_from_form()
        proposal_id = allocation_id = None
        if action in ("accept", "reject", "allocate-proposal"):
            proposal_id = parse_job_id(request.form.get("proposal_id"))
        elif action in ("complete", "fail"):
            allocation_id = parse_job_id(request.form.get("allocation_id"))
        elif action not in ("generate", "allocate"):
            abort(404)
        # Preflight uses a read-only transaction that ends BEFORE the service's BEGIN.
        with read_snapshot():
            view = workspace()
            view.owned_job(job_id, customer_id)
            if allocation_id is not None:
                view.owned_allocation(job_id, allocation_id, customer_id)
        connection = get_connection()
        if action == "generate":
            result = ProposalService(connection).generate(job_id)
            message = ("대안을 생성했습니다. 수락은 예약·배정 완료가 아닙니다." if result.proposals else
                       "현재 조건에서 새 대안이 없습니다. 원본 가용성과 부족 사유를 확인하세요.")
        elif action in ("accept", "reject"):
            result = ProposalService(connection).respond(job_id, proposal_id, customer_id,
                                                         "ACCEPTED" if action == "accept" else "REJECTED")
            message = "고객 의사를 기록했습니다. 수락해도 GPU는 예약되지 않습니다."
        elif action in ("allocate", "allocate-proposal"):
            service = AllocationService(connection)
            result = (service.allocate(job_id) if action == "allocate" else
                      service.allocate_proposal(job_id, proposal_id, customer_id))
            message = "기존 배정 결과를 표시합니다." if result.replayed else "현재 가용성을 재검사하고 GPU를 배정했습니다."
        else:
            result = AllocationService(connection).finish(allocation_id, "COMPLETED" if action == "complete" else "FAILED")
            message = "기존 반환 결과를 표시합니다." if result.replayed else "작업 상태와 전체 GPU 반환을 함께 저장했습니다."
        flash(message, "success")
        return redirect(url_for("job_detail", job_id=job_id, customer_id=customer_id), code=303)

    return app


if __name__ == "__main__":
    create_app().run(host="127.0.0.1", port=5000, debug=False, use_reloader=False)
