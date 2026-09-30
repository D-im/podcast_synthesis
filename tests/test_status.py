import time
from contextlib import closing

import pytest
from fastapi.testclient import TestClient

from app.adapters.fakes import FakeBehavior, build_fakes
from app.core import submit
from app.store import db, episodes
from app.web.app import create_app
from app.worker import Worker

VID = "dQw4w9WgXcQ"
POLL = ('hx-get="/episodes/%s/status"' % VID, 'hx-trigger="every 3s"', 'hx-swap="outerHTML"')


@pytest.fixture
def env(tmp_path):
    db.bootstrap(tmp_path)
    with closing(db.connect(tmp_path)) as c:
        submit.submit(c, f"https://www.youtube.com/watch?v={VID}")
    return TestClient(create_app([], tmp_path)), tmp_path


def jid(d):
    with closing(db.connect(d)) as c:
        return episodes.get_latest_job_with_steps(c, VID)["id"]


def setup_state(d, job_state, steps=None, title=None):
    with closing(db.connect(d)) as c:
        episodes.set_job_state(c, jid(d), job_state)
        for name, (state, msg) in (steps or {}).items():
            episodes.set_step_state(c, jid(d), name, state, msg)
        if title:
            c.execute("UPDATE episodes SET title=? WHERE video_id=?", (title, VID))
        c.commit()


def frag(client):
    r = client.get(f"/episodes/{VID}/status")
    assert r.status_code == 200
    return r.text


def test_queued(env):
    client, _ = env
    t = frag(client)
    assert "Queued" in t and all(a in t for a in POLL) and t.count("pending") == 4
    assert f"<h1>{VID}</h1>" in t


def test_running(env):
    client, d = env
    setup_state(d, "running", {"download": ("done", None), "transcribe": ("running", None)})
    t = frag(client)
    assert "Transcribing" in t and "download: done" in t and all(a in t for a in POLL)


def test_between_steps(env):
    client, d = env
    setup_state(d, "running", {"download": ("done", None), "transcribe": ("done", None)})
    t = frag(client)
    assert "Summarizing" in t and all(a in t for a in POLL)


def test_done_skipped_verify(env):
    client, d = env
    setup_state(d, "done", {s: ("done", None) for s in ("download", "transcribe", "summarize")}
                | {"verify": ("skipped", "no verifier configured")})
    t = frag(client)
    assert "Done" in t and "verify: skipped" in t and "no verifier configured" in t
    assert "hx-get" not in t and "hx-trigger" not in t


def test_failed_and_escaped(env):
    client, d = env
    setup_state(d, "failed", {"download": ("done", None),
                              "transcribe": ("failed", "<script>alert(1)</script>")},
                title="<script>t</script>")
    for t in (frag(client), client.get(f"/episodes/{VID}").text):
        assert "Failed at transcribe" in t
        assert "<script>alert" not in t and "&lt;script&gt;alert(1)" in t
        assert "<script>t" not in t and "&lt;script&gt;t" in t
        assert "summarize: pending" in t and "hx-get" not in t


def test_unknown_episode(env):
    client, _ = env
    assert client.get("/episodes/nope/status").status_code == 404
    assert client.get("/episodes/nope").status_code == 404


def test_page_embeds_fragment_and_local_htmx(env):
    client, _ = env
    t = client.get(f"/episodes/{VID}").text
    assert 'src="/static/htmx.min.js"' in t and "http" not in t.split("<body>")[0]
    assert all(a in t for a in POLL)


def test_static_htmx(env):
    client, _ = env
    r = client.get("/static/htmx.min.js")
    assert r.status_code == 200 and 'version:"2.0.' in r.text


def test_live_progress(env):
    client, d = env
    seen = [frag(client)]
    w = Worker(d, build_fakes(FakeBehavior(), True))
    w.run_next()
    seen.append(frag(client))
    assert "Queued" in seen[0] and "Done" in seen[1] and "hx-get" not in seen[1]


def test_status_response_is_not_cacheable(env):
    client, _ = env
    assert client.get(f"/episodes/{VID}/status").headers["cache-control"] == "no-store"


def test_live_progress_shows_the_step_in_progress(env):
    client, d = env
    mid = {}
    fakes = build_fakes(FakeBehavior(), True)
    real = fakes.transcriber.transcribe

    def transcribe_and_look(audio_path):
        mid["fragment"] = frag(client)  # the page as seen while transcription runs
        return real(audio_path)

    fakes.transcriber.transcribe = transcribe_and_look
    Worker(d, fakes).run_next()
    assert "Transcribing" in mid["fragment"] and all(a in mid["fragment"] for a in POLL)
    assert "download: done" in mid["fragment"]
    assert "Done" in frag(client)


def _job(state, steps):
    return {"state": state, "steps": [{"name": n, "state": s, "message": m} for n, s, m in steps]}


def test_describe_job_orders_steps_and_hides_messages_of_finished_steps():
    from app.web.status import describe_job
    v = describe_job({"video_id": "x", "title": None}, _job("running", [
        ("summarize", "pending", None), ("download", "done", "internal note"),
        ("verify", "pending", None), ("transcribe", "running", None)]))
    assert [s["name"] for s in v["steps"]] == ["download", "transcribe", "summarize", "verify"]
    assert v["label"] == "Transcribing"
    assert all(s["message"] is None for s in v["steps"])


def test_describe_job_fallbacks():
    from app.web.status import describe_job
    ep = {"video_id": "x", "title": None}
    done_steps = [(n, "done", None) for n in ("download", "transcribe", "summarize", "verify")]
    assert describe_job(ep, _job("running", done_steps))["label"] == "Running"
    failed_no_step = describe_job(ep, _job("failed", done_steps))
    assert failed_no_step["label"] == "Failed" and failed_no_step["polling"] is False
    odd = describe_job(ep, _job("cancelled", done_steps))
    assert odd["label"] == "Cancelled" and odd["polling"] is False
