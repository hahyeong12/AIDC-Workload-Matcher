"""Flask adapter tests: service calls, policy gates, rendering and cleanup."""

from dataclasses import replace
import re
from datetime import datetime
from unittest.mock import Mock

import pytest

from app import create_app, web
from app.allocation import AllocationConflict, AllocationGPU, AllocationResult, InsufficientResources
from app.db import DatabaseConfigError, DatabaseOperationError
from app.job import JobDetail
from app.matching import JobRequirement
from app.proposal import CustomerMismatch, CustomerResponse, ProposalConflict, ProposalDetail, ProposalRecord
from database.inspect_schema import definitions


NOW = datetime(2026, 11, 10)
JOB = JobDetail(7, 1, "새 학습", "  고객 원문\n", NOW, 1, 4, True)
REQUIREMENT = JobRequirement(7, 1, "NVIDIA", "H100", 4, True)
PROPOSAL = ProposalRecord(9, 7, 1, 1, 3, True, "REDUCED_QUANTITY", "수량 부족", (), "성능 보장 없음", NOW)
CUSTOMERS = ({"customer_id": 1, "display_name": "시연 고객"},)
MODELS = ({"gpu_model_id": 1, "manufacturer": "NVIDIA", "model_name": "H100", "memory_gb": 80},)
FORM = dict(customer_id="1", title="신규 학습", original_request="  원문\n", gpu_model_id="1",
            gpu_count="2", same_server_required="1")


def report(version="v0.3-2"):
    tables, triggers = definitions(version)
    return dict(schema_version=version, unexpected_tables=0, unexpected_triggers=0,
                tables={name: dict(exists=True, matches=True) for name in tables},
                triggers={name: dict(exists=True, matches=True) for name in triggers})


@pytest.fixture
def ui(monkeypatch):
    cn = Mock(in_transaction=False, web_rollback_only=False)
    monkeypatch.setattr(web, "inspect", Mock(return_value=report()))
    monkeypatch.setattr(web.WorkspaceRepository, "database_name", lambda self: "aidc_demo")
    view = Mock()
    view.catalog.customers.return_value = CUSTOMERS
    view.catalog.models.return_value = MODELS
    view.dashboard.return_value = dict(models=[dict(MODELS[0], total=5, healthy=4, available=3)],
                                      servers=[dict(server_id=1, server_name="server-01", operational_status="ONLINE")],
                                      jobs=[dict(job=JOB, status="REGISTERED")])
    availability = Mock(status="INSUFFICIENT_RESOURCES", available_gpu_count=3, max_gpus_per_server=3,
                        reason_codes=("AVAILABLE_QUANTITY_SHORTAGE",), explanation="자원 부족")
    hardware = Mock(status="HARDWARE_INSUFFICIENT", requirement=REQUIREMENT)
    view.detail.return_value = dict(job=JOB, hardware=hardware, availability=availability, proposals=(), history=())
    view.proposal.return_value = dict(job=JOB, detail=ProposalDetail(PROPOSAL, REQUIREMENT, None),
                                     executed=False)
    monkeypatch.setattr(web, "WorkspaceService", Mock(return_value=view))
    jobs = Mock()
    jobs.register.return_value = 77
    monkeypatch.setattr(web, "JobService", Mock(return_value=jobs))
    proposals, allocations = Mock(), Mock()
    proposals.generate.return_value = Mock(proposals=(PROPOSAL,))
    proposals.respond.return_value = Mock(replayed=False)
    allocations.allocate.return_value = Mock(replayed=False)
    allocations.allocate_proposal.return_value = Mock(replayed=False)
    allocations.finish.return_value = Mock(replayed=False)
    monkeypatch.setattr(web, "ProposalService", Mock(return_value=proposals))
    monkeypatch.setattr(web, "AllocationService", Mock(return_value=allocations))
    app = create_app(dict(TESTING=True, SECRET_KEY="test-secret", WEB_ENABLE_WRITES=True,
                          WEB_WRITE_DATABASE="aidc_demo", WEB_TEST_CONNECT=lambda: cn))
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["csrf"] = "unit-csrf"
    return dict(app=app, client=client, cn=cn, view=view, jobs=jobs, proposals=proposals, allocations=allocations)


def post(ui, path, **values):
    return ui["client"].post(path, data=dict(csrf_token="unit-csrf", customer_id="1", **values))


@pytest.mark.parametrize("path,text", [("/", "운영 대시보드"), ("/jobs/new", "신규 Job 등록"),
    ("/jobs/7?customer_id=1", "원본 요청"), ("/jobs/7/proposals/9?customer_id=1", "GPU 수량 축소 검토")])
def test_render_pages_and_cleanup(ui, path, text):
    response = ui["client"].get(path)
    assert response.status_code == 200
    assert text in response.get_data(as_text=True)
    ui["cn"].close.assert_called_once()
    assert ui["cn"].rollback.call_count >= 2
    ui["cn"].commit.assert_not_called()
    assert all(call.kwargs["readonly"] is True for call in ui["cn"].start_transaction.call_args_list)


def test_factory_defaults_are_safe_and_do_not_connect(monkeypatch):
    monkeypatch.delenv("WEB_ENABLE_WRITES", raising=False)
    monkeypatch.delenv("WEB_SECRET_KEY", raising=False)
    connect = Mock()
    monkeypatch.setattr(web, "connect_database", connect)
    app = create_app()
    assert app.config["WEB_ENABLE_WRITES"] is False
    assert app.config["DEBUG"] is False
    assert app.config["SESSION_COOKIE_HTTPONLY"] is True
    assert app.config["SESSION_COOKIE_SAMESITE"] == "Strict"
    assert len(app.secret_key) >= 32
    connect.assert_not_called()


@pytest.mark.parametrize("path", ["/jobs", "/jobs/preview", "/jobs/7/actions/allocate",
    "/jobs/7/actions/accept", "/jobs/7/actions/complete"])
@pytest.mark.parametrize("token", [None, "wrong", "한글"])
def test_csrf_rejection_before_database(ui, path, token):
    data = {} if token is None else {"csrf_token": token}
    response = ui["client"].post(path, data=data)
    assert response.status_code == 400
    ui["cn"].start_transaction.assert_not_called()
    ui["jobs"].register.assert_not_called()


@pytest.mark.parametrize("setting,value", [("WEB_ENABLE_WRITES", False), ("WEB_ENABLE_WRITES", "1"),
                                         ("WEB_WRITE_DATABASE", "different")])
def test_write_configuration_denies_before_business_service(ui, setting, value):
    ui["app"].config[setting] = value
    assert post(ui, "/jobs/7/actions/allocate").status_code == 403
    ui["allocations"].allocate.assert_not_called()


@pytest.mark.parametrize("name", ["demo_test", "AIDC_WORKLOAD_MATCHER_TEST"])
def test_normal_web_app_refuses_test_database_even_when_enabled(ui, monkeypatch, name):
    monkeypatch.setattr(web.WorkspaceRepository, "database_name", lambda self: name)
    ui["app"].config["WEB_WRITE_DATABASE"] = name
    assert post(ui, "/jobs/7/actions/generate").status_code == 403
    ui["proposals"].generate.assert_not_called()


def test_configured_test_database_alias_is_also_blocked(ui, monkeypatch):
    monkeypatch.setenv("TEST_MYSQL_DATABASE", "aidc_demo")
    assert post(ui, "/jobs/7/actions/allocate").status_code == 403


@pytest.mark.parametrize("version", ["v0.2-2", "v0.3-1"])
def test_old_schema_shows_limited_read_view_and_blocks_all_writes(ui, monkeypatch, version):
    monkeypatch.setattr(web, "inspect", lambda cn: report(version))
    response = ui["client"].get("/jobs/7?customer_id=1")
    assert response.status_code == 200
    assert "대안·근거 기능 제한" in response.get_data(as_text=True)
    web.WorkspaceService.assert_called_with(ui["cn"], advanced=False)
    assert post(ui, "/jobs/7/actions/allocate").status_code == 403
    assert ui["client"].get("/jobs/7/proposals/9?customer_id=1").status_code == 409


@pytest.mark.parametrize("problem", ["missing-trigger", "bad-trigger", "bad-table", "extra-table", "partial"])
def test_unverified_schema_fails_closed(ui, monkeypatch, problem):
    result = report()
    if problem == "missing-trigger": next(iter(result["triggers"].values()))["exists"] = False
    elif problem == "bad-trigger": next(iter(result["triggers"].values()))["matches"] = False
    elif problem == "bad-table": next(iter(result["tables"].values()))["matches"] = False
    elif problem == "extra-table": result["unexpected_tables"] = 1
    else: result["schema_version"] = "partial-v0.3-2"
    monkeypatch.setattr(web, "inspect", lambda cn: result)
    assert post(ui, "/jobs/7/actions/allocate").status_code == 503
    ui["allocations"].allocate.assert_not_called()
    ui["cn"].close.assert_called_once()


def test_confirmation_then_register_calls_existing_service_once(ui):
    response = ui["client"].post("/jobs/preview", data=dict(FORM, csrf_token="unit-csrf"))
    assert response.status_code == 200 and "등록 전 확인" in response.get_data(as_text=True)
    ui["jobs"].register.assert_not_called()
    tickets = ui["app"].extensions["registration_tickets"].pending
    ticket = next(iter(tickets))
    response = post(ui, "/jobs", ticket=ticket)
    assert response.status_code == 303 and "/jobs/77?customer_id=1" in response.location
    ui["jobs"].register.assert_called_once_with(1, FORM["title"], FORM["original_request"], 1, 2, 1)
    assert post(ui, "/jobs", ticket=ticket).status_code == 409
    assert ui["jobs"].register.call_count == 1


@pytest.mark.parametrize("field,value", [("title", ""), ("title", "가" * 201), ("original_request", " "),
    ("original_request", "😀" * 16384), ("gpu_count", "0"), ("gpu_count", "-1"),
    ("gpu_count", "1.5"), ("gpu_count", "2147483648"), ("same_server_required", "true"),
    ("gpu_model_id", "1 OR 1=1"), ("customer_id", "-1")])
def test_invalid_registration_returns_form_with_error_and_no_ticket(ui, field, value):
    values = dict(FORM, csrf_token="unit-csrf")
    values[field] = value
    response = ui["client"].post("/jobs/preview", data=values)
    assert response.status_code == 400
    assert 'role="alert"' in response.get_data(as_text=True)
    assert not ui["app"].extensions["registration_tickets"].pending
    ui["jobs"].register.assert_not_called()


@pytest.mark.parametrize("action,service,method,extras,expected", [
    ("generate", "proposals", "generate", {}, (7,)),
    ("accept", "proposals", "respond", {"proposal_id": "9"}, (7, 9, 1, "ACCEPTED")),
    ("reject", "proposals", "respond", {"proposal_id": "9"}, (7, 9, 1, "REJECTED")),
    ("allocate", "allocations", "allocate", {}, (7,)),
    ("allocate-proposal", "allocations", "allocate_proposal", {"proposal_id": "9"}, (7, 9, 1)),
    ("complete", "allocations", "finish", {"allocation_id": "11"}, (11, "COMPLETED")),
    ("fail", "allocations", "finish", {"allocation_id": "11"}, (11, "FAILED")),
])
def test_post_dispatches_to_existing_service(ui, action, service, method, extras, expected):
    response = post(ui, f"/jobs/7/actions/{action}", **extras)
    assert response.status_code == 303
    getattr(ui[service], method).assert_called_once_with(*expected)
    ui["view"].owned_job.assert_called_with(7, 1)


@pytest.mark.parametrize("error,status", [(InsufficientResources("자원 부족"), 409),
    (AllocationConflict("다른 근거로 배정됨"), 409), (CustomerMismatch("고객 불일치"), 403),
    (ProposalConflict("다른 응답으로 확정됨"), 409),
    (DatabaseOperationError("web-query", "allocations", RuntimeError("secret")), 503),
    (RuntimeError("password=secret SQL traceback"), 503)])
def test_service_errors_are_visible_safely_and_connection_is_cleaned(ui, error, status):
    ui["allocations"].allocate.side_effect = error
    response = post(ui, "/jobs/7/actions/allocate")
    assert response.status_code == status
    assert "secret" not in response.get_data(as_text=True)
    assert "Traceback" not in response.get_data(as_text=True)
    ui["cn"].close.assert_called_once()
    assert ui["cn"].rollback.called


def test_foreign_customer_preflight_never_calls_write_service(ui):
    ui["view"].owned_job.side_effect = CustomerMismatch("고객 불일치")
    assert post(ui, "/jobs/7/actions/generate").status_code == 403
    ui["proposals"].generate.assert_not_called()


def test_foreign_allocation_preflight_never_returns_gpus(ui):
    ui["view"].owned_allocation.side_effect = ProposalConflict("다른 Job 할당")
    assert post(ui, "/jobs/7/actions/complete", allocation_id="999").status_code == 409
    ui["allocations"].finish.assert_not_called()


@pytest.mark.parametrize("path", ["/jobs/7/actions/allocate", "/jobs/preview", "/jobs"])
def test_get_cannot_mutate(ui, path):
    assert ui["client"].get(path).status_code == 405
    ui["allocations"].allocate.assert_not_called()
    ui["jobs"].register.assert_not_called()


def test_xss_escaped_on_detail_and_form(ui):
    payload = '<script>alert("x")</script>'
    ui["view"].detail.return_value["job"] = replace(JOB, title=payload, original_request=payload)
    response = ui["client"].get("/jobs/7?customer_id=1")
    assert payload not in response.get_data(as_text=True)
    assert "&lt;script&gt;" in response.get_data(as_text=True)
    response = ui["client"].post("/jobs/preview", data=dict(FORM, title=payload, original_request=payload, csrf_token="unit-csrf"))
    assert payload not in response.get_data(as_text=True)
    assert "&lt;script&gt;" in response.get_data(as_text=True)


@pytest.mark.parametrize("host,remote", [("evil.example", "127.0.0.1"), ("localhost", "192.0.2.1"),
                                        ("localhost", "::ffff:192.0.2.1")])
def test_host_and_remote_address_limits(ui, host, remote):
    response = ui["client"].get("/", base_url=f"http://{host}", environ_overrides={"REMOTE_ADDR": remote})
    assert response.status_code in (400, 403)
    ui["cn"].start_transaction.assert_not_called()


def test_security_headers_and_session_cookie(ui):
    response = ui["client"].get("/")
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
    assert "'unsafe-inline'" not in response.headers["Content-Security-Policy"]
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Cache-Control"] == "no-store"
    cookie = response.headers.get("Set-Cookie", "")
    # Selection writes a session cookie on the query-string path.
    response = ui["client"].get("/?customer_id=1")
    cookie = response.headers.get("Set-Cookie", cookie)
    assert "HttpOnly" in cookie and "SameSite=Strict" in cookie


def test_read_begin_failure_still_rolls_back_and_closes(ui):
    ui["cn"].start_transaction.side_effect = RuntimeError("private")
    response = ui["client"].get("/")
    assert response.status_code == 503
    assert "private" not in response.get_data(as_text=True)
    assert ui["cn"].rollback.called
    ui["cn"].close.assert_called_once()


def test_teardown_close_runs_even_if_rollback_fails(ui):
    ui["cn"].rollback.side_effect = RuntimeError("private")
    assert ui["client"].get("/").status_code == 503
    ui["cn"].close.assert_called_once()


def test_ticket_is_session_bound_and_expires(ui, monkeypatch):
    tickets = ui["app"].extensions["registration_tickets"]
    with ui["app"].test_request_context():
        ticket = tickets.issue(tuple(FORM.values()), "other-session")
    assert post(ui, "/jobs", ticket=ticket).status_code == 409
    with ui["app"].test_request_context():
        ticket = tickets.issue(tuple(FORM.values()), "unit-csrf")
    monkeypatch.setattr(web, "monotonic", lambda: 10**15)
    assert post(ui, "/jobs", ticket=ticket).status_code == 409


def test_failed_registration_ticket_cannot_be_automatically_retried(ui):
    ui["client"].post("/jobs/preview", data=dict(FORM, csrf_token="unit-csrf"))
    ticket = next(iter(ui["app"].extensions["registration_tickets"].pending))
    ui["jobs"].register.side_effect = DatabaseConfigError("secret")
    assert post(ui, "/jobs", ticket=ticket).status_code == 503
    assert post(ui, "/jobs", ticket=ticket).status_code == 409
    assert ui["jobs"].register.call_count == 1


def button(html, action):
    """The submit button of the action form, e.g. '<button class="button" disabled ...>'."""
    return re.search(rf'action="/jobs/7/actions/{action}".*?(<button[^>]*>)', html, re.S)[1]


@pytest.mark.parametrize("status", ["ACTIVE", "COMPLETED", "FAILED"])
def test_generate_button_is_disabled_once_job_has_allocation_history(ui, status):
    page = ui["client"].get("/jobs/7?customer_id=1").get_data(as_text=True)
    assert "disabled" not in button(page, "generate")       # short of resources, no history
    ended = None if status == "ACTIVE" else NOW
    allocation = AllocationResult(11, 7, status, NOW, ended, (AllocationGPU(1, 1, NOW, ended),))
    ui["view"].detail.return_value["history"] = (allocation,)
    page = ui["client"].get("/jobs/7?customer_id=1").get_data(as_text=True)
    assert "disabled" in button(page, "generate")
    assert "배정 이력이 있는 Job" in page


def test_accept_button_is_disabled_but_reject_allowed_once_job_has_allocation_history(ui):
    page = ui["client"].get("/jobs/7/proposals/9?customer_id=1").get_data(as_text=True)
    assert "disabled" not in button(page, "accept") and "disabled" not in button(page, "reject")
    ui["view"].proposal.return_value["executed"] = True
    page = ui["client"].get("/jobs/7/proposals/9?customer_id=1").get_data(as_text=True)
    assert "disabled" in button(page, "accept")
    assert "disabled" not in button(page, "reject")
    assert "배정 이력이 있는 Job" in page
