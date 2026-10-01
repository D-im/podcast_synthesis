from contextlib import closing

import pytest
from fastapi.testclient import TestClient

from app.adapters.fakes import FakeBehavior, build_fakes
from app.core import submit
from app.store import db, episodes
from app.web.app import create_app
from app.worker import Worker

VID = "dQw4w9WgXcQ"
VID2 = "aaaaaaaaaaa"


@pytest.fixture
def data(tmp_path):
    db.bootstrap(tmp_path)
    return tmp_path


def client(data):
    return TestClient(create_app([], data), follow_redirects=False)


def queue(data, vid=VID):
    with closing(db.connect(data)) as c:
        submit.submit(c, f"https://www.youtube.com/watch?v={vid}")


def run(data, **kw):
    b = FakeBehavior(**kw)
    assert Worker(data, build_fakes(b)).run_next()
    return b


def job(data, vid=VID):
    with closing(db.connect(data)) as c:
        return episodes.get_latest_job_with_steps(c, vid)


def step(data, name, vid=VID):
    return next(s for s in job(data, vid)["steps"] if s["name"] == name)


def ledger(data):
    with closing(db.connect(data)) as c:
        return c.execute("SELECT step, amount_micro_usd FROM spend_ledger ORDER BY id").fetchall()


def failed(data, at="summarize", retryable=True, message="boom", vid=VID):
    queue(data, vid)
    return run(data, fail_at=at, retryable=retryable, message=message, cost_micro=5)


def retry(data, vid=VID):
    return client(data).post(f"/episodes/{vid}/retry")


def test_failed_retryable_shows_step_message_attempts_button(data):
    failed(data)
    page = client(data).get(f"/episodes/{VID}").text
    assert "Failed step: summarize. Attempts: 1" in page and "boom" in page
    assert 'action="/episodes/dQw4w9WgXcQ/retry"' in page and ">Retry<" in page


def test_retry_requeues_same_job_and_resumes_without_new_spend_for_done_steps(data):
    failed(data)
    before = job(data)
    before_ledger = ledger(data)
    r = retry(data)
    assert r.status_code == 303 and r.headers["location"] == f"/episodes/{VID}"
    after = job(data)
    assert after["id"] == before["id"] and after["state"] == "queued"
    states = {s["name"]: s["state"] for s in after["steps"]}
    assert states == {"download": "done", "transcribe": "done", "summarize": "pending",
                      "verify": "pending"}
    assert step(data, "summarize")["message"] is None
    assert ledger(data) == before_ledger          # nothing written at retry time
    b = run(data, cost_micro=5)
    assert b.calls == ["summarize", "verify"]     # restarted at the failed step
    assert job(data)["state"] == "done"
    assert [r[0] for r in ledger(data)][len(before_ledger):] == ["summarize", "verify"]


def test_not_retryable_shows_explanation_no_button_and_post_refused(data):
    failed(data, retryable=False)
    page = client(data).get(f"/episodes/{VID}").text
    assert "This failure is permanent" in page and ">Retry<" not in page and "boom" in page
    r = retry(data)
    assert r.status_code == 409 and "cannot be retried" in r.text
    assert job(data)["state"] == "failed"


def test_unknown_retryable_treated_as_not_retryable(data):
    failed(data)
    with closing(db.connect(data)) as c:
        c.execute("UPDATE steps SET retryable = NULL WHERE state = 'failed'")
    assert ">Retry<" not in client(data).get(f"/episodes/{VID}").text
    assert retry(data).status_code == 409


def test_attempts_count_up_across_retries(data):
    failed(data)
    assert step(data, "summarize")["attempts"] == 1
    retry(data)
    run(data, fail_at="summarize", message="again")
    assert step(data, "summarize")["attempts"] == 2 and step(data, "summarize")["message"] == "again"
    assert "Attempts: 2" in client(data).get(f"/episodes/{VID}").text
    retry(data)
    run(data, fail_at="summarize")
    assert "Attempts: 3" in client(data).get(f"/episodes/{VID}").text


def test_old_failed_step_with_zero_attempts_shows_one(data):
    failed(data)
    with closing(db.connect(data)) as c:
        c.execute("UPDATE steps SET attempts = 0")
    assert "Attempts: 1" in client(data).get(f"/episodes/{VID}").text


@pytest.mark.parametrize("state", ["queued", "running", "paused", "done"])
def test_other_states_not_retried(data, state):
    queue(data)
    with closing(db.connect(data)) as c:
        episodes.set_job_state(c, job(data)["id"], state)
    page = client(data).get(f"/episodes/{VID}").text
    assert ">Retry<" not in page
    r = retry(data)
    assert r.status_code == 409 and job(data)["state"] == state


def test_active_newer_job_blocks(data):
    failed(data)
    with closing(db.connect(data)) as c:
        episodes.enqueue_regeneration(c, VID, ["download", "transcribe", "summarize", "verify"],
                                      ["download", "transcribe"])
    r = retry(data)
    assert r.status_code == 409 and "waiting or running" in r.text
    with closing(db.connect(data)) as c:
        assert c.execute("SELECT state FROM jobs WHERE id = 1").fetchone()[0] == "failed"


def test_unknown_episode_404_and_no_job_409(data):
    assert retry(data, "zzzzzzzzzzz").status_code == 404
    with closing(db.connect(data)) as c:
        c.execute("INSERT INTO episodes (video_id, url, created_at) VALUES (?, 'u', 't')", (VID2,))
    assert retry(data, VID2).status_code == 409


def test_double_submit_retries_once(data):
    failed(data)
    assert retry(data).status_code == 303
    r = retry(data)
    assert r.status_code == 409 and "waiting or running" in r.text
    assert step(data, "summarize")["attempts"] == 1


def test_get_retry_changes_nothing(data):
    failed(data)
    assert client(data).get(f"/episodes/{VID}/retry").status_code in (404, 405)
    assert job(data)["state"] == "failed"


def test_queue_order_retried_job_waits_for_earlier_job(data):
    failed(data)
    queue(data, VID2)                          # queued after the failed job (higher id)
    retry(data)                                # keeps its lower id, so it runs first
    assert job(data)["id"] < job(data, VID2)["id"]
    run(data)
    assert job(data)["state"] == "done" and job(data, VID2)["state"] == "queued"


def test_retry_from_verify_runs_only_verify_and_keeps_versions(data):
    failed(data, at="verify")
    with closing(db.connect(data)) as c:
        versions = episodes.list_one_pager_versions(c, VID)
    retry(data)
    b = run(data)
    assert b.calls == ["verify"]
    with closing(db.connect(data)) as c:
        assert episodes.list_one_pager_versions(c, VID) == versions


def test_retry_resumes_saved_vendor_job_id(data):
    failed(data, at="transcribe")
    with closing(db.connect(data)) as c:
        episodes.set_vendor_job_id(c, job(data)["id"], "transcribe", "vendor-1")
    retry(data)
    with closing(db.connect(data)) as c:
        assert episodes.get_vendor_job_id(c, job(data)["id"], "transcribe") == "vendor-1"


def test_hostile_message_escaped(data):
    failed(data, message="<script>alert(1)</script>")
    page = client(data).get(f"/episodes/{VID}").text
    assert "<script>alert(1)" not in page and "&lt;script&gt;" in page


def test_recovery_does_not_reduce_attempts(data):
    from app.core.recovery import recover
    queue(data)
    with closing(db.connect(data)) as c:
        jid = job(data)["id"]
        episodes.set_job_state(c, jid, "running")
        episodes.set_step_state(c, jid, "download", "running")
        recover(c, data)
    assert step(data, "download")["attempts"] == 1
