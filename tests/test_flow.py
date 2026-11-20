import time

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.extract import ValidationError, parse_invoice

OK = b"Vendor: Acme\nInvoice No: A-1\nDate: 2026-01-02\nWidget 2 x 5.00\nTotal: 10.00\n"
BAD_TOTAL = b"Vendor: Acme\nWidget 2 x 5.00\nTotal: 99.00\n"
CRASH = b"Vendor: Acme\nSIMULATE_CRASH\nTotal: 1.00\n"


def h(user):
    return {"Authorization": f"Bearer demo:{user}"}


@pytest.fixture
def client(tmp_path):
    settings = Settings(
        data_dir=tmp_path, visibility_timeout=0.3, processing_delay=0, poll_interval=0.05,
        max_upload_bytes=1024,
    )
    with TestClient(create_app(settings)) as c:
        yield c


def submit(c, user, filename, content):
    r = c.post("/jobs", json={"filename": filename}, headers=h(user))
    assert r.status_code == 201, r.text
    job = r.json()
    up = c.put(job["upload_url"], content=content)
    assert up.status_code == 204, up.text
    return job["job_id"]


def wait_for(c, user, job_id, statuses, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        job = c.get(f"/jobs/{job_id}", headers=h(user)).json()
        if job["status"] in statuses:
            return job
        time.sleep(0.05)
    raise AssertionError(f"job stuck in {job['status']}")


def test_happy_path_and_notification(client):
    job_id = submit(client, "alice", "inv.txt", OK)
    job = wait_for(client, "alice", job_id, {"DONE", "FAILED"})
    assert job["status"] == "DONE"
    assert job["result"]["total"] == 10.0
    assert job["result"]["vendor"] == "Acme"
    assert job["result"]["invoice_number"] == "A-1"
    mails = client.get("/notifications", headers=h("alice")).json()
    assert any("inv.txt" in m["subject"] for m in mails)


def test_validation_failure_is_final_not_retried(client):
    job_id = submit(client, "alice", "bad.txt", BAD_TOTAL)
    job = wait_for(client, "alice", job_id, {"DONE", "FAILED"})
    assert job["status"] == "FAILED"
    assert "add up" in job["error"]
    assert client.get("/admin/queue", headers=h("admin")).json()["dead_letter"] == 0


def test_crash_retries_then_dead_letter_then_replay(client):
    job_id = submit(client, "alice", "crash.txt", CRASH)
    job = wait_for(client, "alice", job_id, {"FAILED"}, timeout=15)
    assert "dead-letter" in job["error"]
    assert client.get("/admin/queue", headers=h("admin")).json()["dead_letter"] == 1
    assert client.post("/admin/dlq/replay", headers=h("admin")).json()["replayed"] == 1


def test_users_cannot_see_each_others_jobs(client):
    job_id = submit(client, "alice", "inv.txt", OK)
    assert client.get(f"/jobs/{job_id}", headers=h("bob")).status_code == 404
    assert client.get("/jobs", headers=h("bob")).json() == []


def test_auth_required_and_admin_only(client):
    assert client.get("/jobs").status_code == 401
    assert client.get("/admin/queue", headers=h("alice")).status_code == 403


def test_status_reads_are_cached(client):
    job_id = submit(client, "alice", "inv.txt", OK)
    wait_for(client, "alice", job_id, {"DONE"})
    first = client.get(f"/jobs/{job_id}", headers=h("alice"))
    second = client.get(f"/jobs/{job_id}", headers=h("alice"))
    assert second.headers["X-Cache"] == "HIT"
    assert first.json() == second.json()


def test_rejects_bad_type_oversize_and_reused_link(client):
    assert client.post("/jobs", json={"filename": "x.exe"}, headers=h("alice")).status_code == 400
    r = client.post("/jobs", json={"filename": "big.txt"}, headers=h("alice")).json()
    assert client.put(r["upload_url"], content=b"x" * 2048).status_code == 413
    job_id = submit(client, "alice", "inv.txt", OK)
    wait_for(client, "alice", job_id, {"DONE"})
    r2 = client.post("/jobs", json={"filename": "again.txt"}, headers=h("alice")).json()
    assert client.put(r2["upload_url"], content=OK).status_code == 204
    assert client.put(r2["upload_url"], content=OK).status_code == 403


def test_parser_directly():
    assert parse_invoice("Vendor: V\nTotal: 5.00")["total"] == 5.0
    with pytest.raises(ValidationError):
        parse_invoice("Vendor: V")
    with pytest.raises(ValidationError):
        parse_invoice("   ")


def test_health_reports_version(client):
    body = client.get("/health").json()
    assert body["ok"] is True
    assert body["version"]
