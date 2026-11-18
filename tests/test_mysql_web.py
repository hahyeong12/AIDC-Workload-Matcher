"""Real Flask -> services -> MySQL SQL. All new originals roll back externally."""

from contextlib import closing
import re

import pytest

from allocation_helpers import execute
from app import create_app
from app.db import connection_settings
from app.services.jobs import JobService
from mysql_audit import snapshot


pytestmark = pytest.mark.mysql


class WebRollbackConnection:
    """Translate every web read/service transaction to one savepoint.

    The fixture owns the real transaction and connection. Neither service COMMIT
    nor request teardown can persist/delete its immutable originals.
    """
    web_rollback_only = True

    def __init__(self, connection):
        self.connection = connection
        self.in_transaction = False
        self.closed = False
        self.readonly = None

    def cursor(self):
        return self.connection.cursor()

    def start_transaction(self, **settings):
        assert not self.in_transaction
        assert settings in ({"isolation_level": "READ COMMITTED"},
                            {"isolation_level": "REPEATABLE READ", "readonly": True, "consistent_snapshot": True})
        self.readonly = settings.get("readonly", False)
        execute(self.connection, "SAVEPOINT web_rollback_envelope")
        self.in_transaction = True

    def commit(self):
        assert self.in_transaction and not self.readonly
        execute(self.connection, "RELEASE SAVEPOINT web_rollback_envelope")
        self.in_transaction = False

    def rollback(self):
        if self.in_transaction:
            execute(self.connection, "ROLLBACK TO SAVEPOINT web_rollback_envelope")
            execute(self.connection, "RELEASE SAVEPOINT web_rollback_envelope")
            self.in_transaction = False

    def close(self):
        assert not self.in_transaction
        self.closed = True


@pytest.fixture
def browser(db, sample):
    opened = []
    def connect():
        connection = WebRollbackConnection(db)
        opened.append(connection)
        return connection
    app = create_app(dict(TESTING=True, SECRET_KEY="mysql-web-tests", WEB_ENABLE_WRITES=True,
                          WEB_WRITE_DATABASE=connection_settings("TEST_MYSQL_")["database"],
                          WEB_TEST_CONNECT=connect, WEB_TEST_ROLLBACK_ONLY=True))
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["csrf"] = "mysql-csrf"
    # The legacy sample Job is intentionally missing requirements. Complete it
    # privately so dashboard list validation remains strict and meaningful.
    execute(db, "INSERT INTO job_requirements VALUES (%s,%s,%s,%s)",
            (sample["job"], sample["model"], 1, 0))
    yield dict(app=app, client=client, db=db, sample=sample, opened=opened)
    assert all(connection.closed and not connection.in_transaction for connection in opened)


def post(browser, path, **values):
    data = dict(csrf_token="mysql-csrf", customer_id=str(browser["sample"]["customer"]))
    data.update(values)
    return browser["client"].post(path, data=data)


def register(browser, count=1, same="1", **overrides):
    sample = browser["sample"]
    values = dict(title="웹 신규 학습", original_request="  원문과 조건을 따로 저장\n",
                  gpu_model_id=str(sample["model"]), gpu_count=str(count), same_server_required=same)
    values.update(overrides)
    preview = post(browser, "/jobs/preview", **values)
    assert preview.status_code == 200, preview.get_data(as_text=True)
    ticket = re.search(r'name="ticket" value="([^"]+)"', preview.get_data(as_text=True))[1]
    result = post(browser, "/jobs", ticket=ticket)
    assert result.status_code == 303, result.get_data(as_text=True)
    job = int(re.search(r"/jobs/(\d+)", result.location)[1])
    return job, ticket


def proposals(browser, job):
    return execute(browser["db"], "SELECT proposal_id FROM job_proposals WHERE job_request_id=%s ORDER BY proposal_id", (job,))


def test_pages_registration_raw_original_and_customer_list(browser):
    job, ticket = register(browser)
    customer = browser["sample"]["customer"]
    detail = JobService(browser["db"]).detail(job)
    assert detail.original_request == "  원문과 조건을 따로 저장\n" and detail.gpu_count == 1
    for path in [f"/?customer_id={customer}", f"/jobs/{job}?customer_id={customer}", f"/jobs/new?customer_id={customer}"]:
        response = browser["client"].get(path)
        assert response.status_code == 200
        assert "웹 신규 학습" in response.get_data(as_text=True) or "/jobs/new" in path
    assert post(browser, "/jobs", ticket=ticket).status_code == 409
    assert len(execute(browser["db"], "SELECT job_request_id FROM job_requests WHERE customer_id=%s",
                       (customer,))) == 2


@pytest.mark.parametrize("ending,expected", [("complete", "COMPLETED"), ("fail", "FAILED")])
def test_original_matching_allocation_replay_and_return(browser, ending, expected):
    job, _ = register(browser)
    customer = browser["sample"]["customer"]
    response = browser["client"].get(f"/jobs/{job}?customer_id={customer}")
    assert "원본 조건 가용" in response.get_data(as_text=True)
    for _ in range(2): assert post(browser, f"/jobs/{job}/actions/allocate").status_code == 303
    rows = execute(browser["db"], "SELECT allocation_id FROM allocations WHERE job_request_id=%s", (job,))
    assert len(rows) == 1
    allocation = rows[0][0]
    page = browser["client"].get(f"/jobs/{job}?customer_id={customer}").get_data(as_text=True)
    assert "ORIGINAL" in page and str(browser["sample"]["gpu"]) in page
    for _ in range(2):
        assert post(browser, f"/jobs/{job}/actions/{ending}", allocation_id=str(allocation)).status_code == 303
    assert execute(browser["db"], "SELECT status FROM allocations WHERE allocation_id=%s", (allocation,)) == [(expected,)]
    assert execute(browser["db"], "SELECT COUNT(*) FROM allocation_gpus WHERE allocation_id=%s AND released_at IS NULL",
                   (allocation,)) == [(0,)]
    opposite = "fail" if ending == "complete" else "complete"
    assert post(browser, f"/jobs/{job}/actions/{opposite}", allocation_id=str(allocation)).status_code == 409


@pytest.mark.parametrize("decision", ["accept", "reject"])
def test_proposal_comparison_response_and_replay(browser, decision):
    job, _ = register(browser, count=2)
    for _ in range(2): assert post(browser, f"/jobs/{job}/actions/generate").status_code == 303
    identifiers = proposals(browser, job)
    assert len(identifiers) == 1
    proposal = identifiers[0][0]
    page = browser["client"].get(f"/jobs/{job}/proposals/{proposal}?customer_id={browser['sample']['customer']}")
    assert page.status_code == 200 and "수량 축소" in page.get_data(as_text=True)
    assert "수락도 실제 배정 성공을 의미하지 않습니다" in page.get_data(as_text=True)
    assert post(browser, f"/jobs/{job}/actions/allocate-proposal", proposal_id=str(proposal)).status_code == 409
    for _ in range(2):
        assert post(browser, f"/jobs/{job}/actions/{decision}", proposal_id=str(proposal)).status_code == 303
    opposite = "reject" if decision == "accept" else "accept"
    assert post(browser, f"/jobs/{job}/actions/{opposite}", proposal_id=str(proposal)).status_code == 409
    assert execute(browser["db"], "SELECT COUNT(*) FROM proposal_responses WHERE job_request_id=%s", (job,)) == [(1,)]
    assert execute(browser["db"], "SELECT COUNT(*) FROM allocations WHERE job_request_id=%s", (job,)) == [(0,)]


def test_accepted_proposal_allocation_evidence_and_original_unchanged(browser):
    job, _ = register(browser, count=2)
    assert post(browser, f"/jobs/{job}/actions/generate").status_code == 303
    proposal = proposals(browser, job)[0][0]
    assert post(browser, f"/jobs/{job}/actions/accept", proposal_id=str(proposal)).status_code == 303
    assert post(browser, f"/jobs/{job}/actions/allocate-proposal", proposal_id=str(proposal)).status_code == 303
    assert post(browser, f"/jobs/{job}/actions/allocate").status_code == 409
    basis = execute(browser["db"], "SELECT basis_type,proposal_id,gpu_count FROM allocation_bases WHERE job_request_id=%s", (job,))
    assert basis == [("ACCEPTED_PROPOSAL", proposal, 1)]
    assert JobService(browser["db"]).detail(job).gpu_count == 2
    page = browser["client"].get(f"/jobs/{job}?customer_id={browser['sample']['customer']}")
    assert "ACCEPTED_PROPOSAL" in page.get_data(as_text=True) and "응답 #" in page.get_data(as_text=True)


def test_stale_accepted_proposal_fails_recheck_without_partial_allocation(browser):
    job, _ = register(browser, count=2)
    post(browser, f"/jobs/{job}/actions/generate")
    proposal = proposals(browser, job)[0][0]
    post(browser, f"/jobs/{job}/actions/accept", proposal_id=str(proposal))
    other, _ = register(browser)
    assert post(browser, f"/jobs/{other}/actions/allocate").status_code == 303
    before = snapshot(browser["db"], "v0.3-2")
    result = post(browser, f"/jobs/{job}/actions/allocate-proposal", proposal_id=str(proposal))
    assert result.status_code == 409 and "필요" in result.get_data(as_text=True)
    assert snapshot(browser["db"], "v0.3-2") == before


@pytest.mark.parametrize("field,value", [("title", " "), ("original_request", ""), ("gpu_count", "0"),
    ("same_server_required", "2"), ("gpu_model_id", str(2**64-1)), ("customer_id", str(2**64-1)),
    ("gpu_model_id", "1 OR 1=1")])
def test_invalid_web_registration_changes_nothing(browser, field, value):
    before = snapshot(browser["db"], "v0.3-2")
    form = dict(title="bad", original_request="original", gpu_model_id=str(browser["sample"]["model"]),
                gpu_count="1", same_server_required="0")
    form[field] = value
    result = post(browser, "/jobs/preview", **form)
    assert result.status_code in (400, 404)
    assert snapshot(browser["db"], "v0.3-2") == before


def test_customer_mismatch_and_foreign_job_proposal_allocation(browser):
    job, _ = register(browser, count=2)
    post(browser, f"/jobs/{job}/actions/generate")
    proposal = proposals(browser, job)[0][0]
    other, _ = register(browser)
    post(browser, f"/jobs/{other}/actions/allocate")
    allocation = execute(browser["db"], "SELECT allocation_id FROM allocations WHERE job_request_id=%s", (other,))[0][0]
    before = snapshot(browser["db"], "v0.3-2")
    for action in ("accept", "reject", "allocate-proposal"):
        assert post(browser, f"/jobs/{other}/actions/{action}", proposal_id=str(proposal)).status_code == 409
    assert post(browser, f"/jobs/{job}/actions/complete", allocation_id=str(allocation)).status_code == 409
    assert browser["client"].get(f"/jobs/{job}?customer_id=1").status_code == 403
    response = browser["client"].post(f"/jobs/{job}/actions/allocate", data=dict(csrf_token="mysql-csrf", customer_id="1"))
    assert response.status_code == 403
    assert browser["client"].get(f"/jobs/{other}/proposals/{proposal}?customer_id={browser['sample']['customer']}").status_code == 409
    assert snapshot(browser["db"], "v0.3-2") == before


def test_sql_injection_text_is_data_and_html_escaped(browser):
    payload = "<script>alert('x')</script>'); DROP TABLE customers; --"
    job, _ = register(browser, title=payload, original_request=payload)
    detail = JobService(browser["db"]).detail(job)
    assert detail.original_request == payload
    page = browser["client"].get(f"/jobs/{job}?customer_id={browser['sample']['customer']}").get_data(as_text=True)
    assert "<script>alert" not in page and "&lt;script&gt;" in page
    assert len(execute(browser["db"], "SELECT customer_id FROM customers")) >= 4


def test_actual_second_insert_failure_rolls_back_web_registration(browser, monkeypatch):
    from app.repositories.jobs import JobRepository
    before = snapshot(browser["db"], "v0.3-2")
    original = JobRepository.insert_requirement
    monkeypatch.setattr(JobRepository, "insert_requirement", lambda self, job, model, count, same:
                        original(self, job, model, 0, same))
    response = post(browser, "/jobs/preview", title="rollback", original_request="original",
                    gpu_model_id=str(browser["sample"]["model"]), gpu_count="1", same_server_required="0")
    ticket = re.search(r'name="ticket" value="([^"]+)"', response.get_data(as_text=True))[1]
    result = post(browser, "/jobs", ticket=ticket)
    assert result.status_code == 503 and "INSERT" not in result.get_data(as_text=True)
    assert snapshot(browser["db"], "v0.3-2") == before


def test_actual_allocation_error_rolls_back_and_closes_connection(browser, monkeypatch):
    from app.repositories.allocations import AllocationRepository
    job, _ = register(browser)
    before = snapshot(browser["db"], "v0.3-2")
    def fail_basis(self, allocation_id, basis):
        self.query("INSERT INTO allocation_bases(allocation_id) VALUES(%s)", (allocation_id,), write=True)
    monkeypatch.setattr(AllocationRepository, "create_basis", fail_basis)
    assert post(browser, f"/jobs/{job}/actions/allocate").status_code == 503
    assert snapshot(browser["db"], "v0.3-2") == before
    assert browser["opened"][-1].closed


def test_web_test_db_writes_require_explicit_rollback_test_adapter(browser):
    browser["app"].config["WEB_TEST_ROLLBACK_ONLY"] = False
    before = snapshot(browser["db"], "v0.3-2")
    assert post(browser, f"/jobs/{browser['sample']['job']}/actions/allocate").status_code == 403
    assert snapshot(browser["db"], "v0.3-2") == before


def test_distributed_proposal_comparison_and_two_server_allocation(browser):
    sample, db = browser["sample"], browser["db"]
    server = execute(db, "INSERT INTO servers(server_name) VALUES (%s)", (sample["token"] + "distributed",))
    execute(db, "INSERT INTO gpus(asset_tag,gpu_model_id,server_id) VALUES (%s,%s,%s)",
            (sample["token"] + "distributed", sample["model"], server))
    job, _ = register(browser, count=2)
    assert post(browser, f"/jobs/{job}/actions/generate").status_code == 303
    proposal = execute(db, "SELECT proposal_id FROM job_proposals WHERE job_request_id=%s AND proposal_type=%s",
                       (job, "DISTRIBUTED"))[0][0]
    response = browser["client"].get(f"/jobs/{job}/proposals/{proposal}?customer_id={sample['customer']}")
    assert response.status_code == 200
    assert "분산 배치 허용 검토" in response.get_data(as_text=True)
    assert "조건 완화" in response.get_data(as_text=True)
    assert post(browser, f"/jobs/{job}/actions/accept", proposal_id=str(proposal)).status_code == 303
    assert post(browser, f"/jobs/{job}/actions/allocate-proposal", proposal_id=str(proposal)).status_code == 303
    rows = execute(db, "SELECT ag.server_id FROM allocation_gpus ag JOIN allocations a "
                   "ON a.allocation_id=ag.allocation_id WHERE a.job_request_id=%s", (job,))
    assert {row[0] for row in rows} == {sample["server"], server}
    assert JobService(db).detail(job).same_server_required is True


def test_live_development_web_is_readonly_without_schema_changes():
    from app.db import connect_database
    with closing(connect_database("MYSQL_")) as cn:
        cn.start_transaction(readonly=True)
        before = snapshot(cn, "v0.2-2")
        cn.rollback()
        app = create_app(dict(TESTING=True, WEB_ENABLE_WRITES=False))
        with app.test_client() as client:
            for path in ("/", "/jobs/new?customer_id=1", "/jobs/3?customer_id=2"):
                response = client.get(path)
                assert response.status_code == 200 and "읽기 전용" in response.get_data(as_text=True)
            with client.session_transaction() as sess:
                token = sess["csrf"]
            response = client.post("/jobs/3/actions/generate", data=dict(customer_id="2", csrf_token=token))
            assert response.status_code == 403
        cn.start_transaction(readonly=True)
        assert snapshot(cn, "v0.2-2") == before
        cn.rollback()


def test_job_with_allocation_history_refuses_new_alternatives_and_acceptance(browser):
    # F1: Job short of GPUs gets a reduced proposal, then the original runs.
    sample, db = browser["sample"], browser["db"]
    job, _ = register(browser, count=2)
    assert post(browser, f"/jobs/{job}/actions/generate").status_code == 303
    proposal = proposals(browser, job)[0][0]
    execute(db, "INSERT INTO gpus(asset_tag,gpu_model_id,server_id) VALUES (%s,%s,%s)",
            (sample["token"] + "late", sample["model"], sample["server"]))
    assert post(browser, f"/jobs/{job}/actions/allocate").status_code == 303
    before = snapshot(db, "v0.3-2")
    assert post(browser, f"/jobs/{job}/actions/generate").status_code == 409
    accepted = post(browser, f"/jobs/{job}/actions/accept", proposal_id=str(proposal))
    assert accepted.status_code == 409 and "배정 이력" in accepted.get_data(as_text=True)
    assert snapshot(db, "v0.3-2") == before
    page = browser["client"].get(f"/jobs/{job}/proposals/{proposal}?customer_id={sample['customer']}")
    assert page.status_code == 200 and "배정 이력이 있는 Job" in page.get_data(as_text=True)
    assert post(browser, f"/jobs/{job}/actions/reject", proposal_id=str(proposal)).status_code == 303
    assert execute(db, "SELECT decision FROM proposal_responses WHERE job_request_id=%s", (job,)) == [("REJECTED",)]
