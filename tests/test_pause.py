import re
from contextlib import closing

import pytest
from fastapi.testclient import TestClient

from app.adapters.fakes import FakeBehavior, build_fakes
from app.config import PROJECT_ROOT
from app.core import guard, submit
from app.store import artifacts, db, episodes
from app.web.app import create_app
from app.worker import Worker

VID, VID2 = "dQw4w9WgXcQ", "aaaaaaaaaaa"
FORM = {"content-type": "application/x-www-form-urlencoded"}
STEPS = ("download", "transcribe", "summarize", "verify")


@pytest.fixture
def data(tmp_path):
    db.bootstrap(tmp_path)
    return tmp_path


@pytest.fixture
def cfg(tmp_path):
    p = tmp_path / "real.toml"
    p.write_text((PROJECT_ROOT / "config.toml").read_text())
    return p


def set_cap(cfg, usd):
    cfg.write_text(re.sub(r"daily_cap_usd = [0-9.]+", f"daily_cap_usd = {usd}", cfg.read_text()))


def queue(data, vid=VID):
    with closing(db.connect(data)) as c:
        submit.submit(c, f"https://www.youtube.com/watch?v={vid}")


def worker(data, cfg, cost=200_000):
    b = FakeBehavior(cost_micro=cost)
    return Worker(data, build_fakes(b), config_path=cfg), b


def job(data, vid=VID):
    with closing(db.connect(data)) as c:
        return episodes.get_latest_job_with_steps(c, vid)


def states(data, vid=VID):
    return {s["name"]: s["state"] for s in job(data, vid)["steps"]}


def ledger(data):
    with closing(db.connect(data)) as c:
        return [r[0] for r in c.execute("SELECT step FROM spend_ledger ORDER BY id")]


def set_duration(data, seconds, vid=VID):
    with closing(db.connect(data)) as c:
        c.execute("UPDATE episodes SET duration_seconds = ? WHERE video_id = ?", (seconds, vid))


def client(data, cfg):
    return TestClient(create_app([], data, config_path=cfg), follow_redirects=False)


# $0.20 per fake step. Estimates: transcribe $0.23 (3600 s), summarize about $0.09, verify $0
# with the fake verifier... the real config is used, so verify is estimated at the real price.
def crossing(data, cfg):
    set_cap(cfg, 0.5)
    queue(data)
    set_duration(data, 3600)
    w, b = worker(data, cfg)
    assert w.run_next()
    return w, b


def test_job_pauses_at_the_step_that_would_cross_the_cap_and_keeps_artifacts(data, cfg):
    crossing(data, cfg)
    j = job(data)
    assert j["state"] == "paused" and "Daily Cap" in j["pause_reason"]
    s = states(data)
    assert s["download"] == "done" and s["transcribe"] == "done"
    assert s["verify"] == "pending"                       # paused at a step boundary
    for name in (artifacts.AUDIO, artifacts.TRANSCRIPT):
        assert artifacts.exists(artifacts.artifact_path(data, VID, name))
    assert [r for r in ledger(data)].count("verify") == 0


def test_page_shows_paused_reason_and_no_resume_until_budget_allows(data, cfg):
    crossing(data, cfg)
    c = client(data, cfg)
    page = c.get(f"/episodes/{VID}").text
    assert "Paused because of the Daily Cap" in page and "Paused before" in page
    assert ">Resume<" not in page and "Cannot resume yet" in page
    assert "nothing resumes by itself" in page
    set_cap(cfg, 50.0)
    assert job(data)["state"] == "paused"                 # nothing resumed by itself
    assert ">Resume<" in c.get(f"/episodes/{VID}").text


def test_resume_refused_while_over_cap_and_for_non_paused(data, cfg):
    crossing(data, cfg)
    c = client(data, cfg)
    r = c.post(f"/episodes/{VID}/resume")
    assert r.status_code == 409 and "Cannot resume yet" in r.text and job(data)["state"] == "paused"
    assert c.post("/episodes/zzzzzzzzzzz/resume").status_code == 404
    queue(data, VID2)
    assert c.post(f"/episodes/{VID2}/resume").status_code == 409


def test_resume_continues_from_paused_step_without_repeating_paid_work(data, cfg):
    w, b = crossing(data, cfg)
    before = ledger(data)
    set_cap(cfg, 50.0)
    r = client(data, cfg).post(f"/episodes/{VID}/resume")
    assert r.status_code == 303 and job(data)["state"] == "queued"
    assert job(data)["pause_reason"] is None
    assert w.run_next()
    assert job(data)["state"] == "done"
    after = ledger(data)
    assert after[:len(before)] == before                  # no earlier work repeated
    assert after[len(before):] == ["verify"]   # only the unfinished step paid
    assert b.calls.count("transcribe") == 1 and b.calls.count("download") == 1


def test_paused_job_does_not_block_other_jobs(data, cfg):
    set_cap(cfg, 0.5)
    queue(data)
    set_duration(data, 3600)
    w, _ = worker(data, cfg)
    w.run_next()
    assert job(data)["state"] == "paused"
    queue(data, VID2)
    set_cap(cfg, 50.0)                                     # room for the second Job
    assert w.run_next()
    assert job(data, VID2)["state"] == "done" and job(data)["state"] == "paused"


def test_queued_job_hitting_cap_at_first_paid_step_pauses(data, cfg):
    set_cap(cfg, 0.5)
    queue(data)
    set_duration(data, 3600)
    with closing(db.connect(data)) as c:
        c.execute("INSERT INTO episodes (video_id, url, created_at) VALUES ('old', 'u', 't')")
        from app.core.meter import StepMeter
        StepMeter(c, "old", "summarize").record("anthropic", 400_000)
    w, _ = worker(data, cfg)
    w.run_next()
    s = states(data)
    assert job(data)["state"] == "paused" and s["download"] == "done" and s["transcribe"] == "pending"


def test_overrun_is_counted_by_the_next_check_using_actual_spend(data, cfg):
    set_cap(cfg, 0.6)
    queue(data)
    w, _ = worker(data, cfg, cost=300_000)                # every step really costs $0.30
    w.run_next()
    s = states(data)
    # download $0.30; transcribe estimated $0.23 fits ($0.53) but really costs $0.30, so
    # today is $0.60 at the summarize check, which refuses on actual spend
    assert job(data)["state"] == "paused" and s["transcribe"] == "done" and s["summarize"] == "pending"


def test_free_steps_never_pause_even_at_the_cap(data, cfg):
    set_cap(cfg, 0.5)
    queue(data)
    with closing(db.connect(data)) as c:
        c.execute("INSERT INTO episodes (video_id, url, created_at) VALUES ('old', 'u', 't')")
        from app.core.meter import StepMeter
        StepMeter(c, "old", "summarize").record("anthropic", 9_000_000)   # far over the cap
    set_duration(data, 0)                                 # transcribe estimated at $0
    # a step estimated at nothing is allowed whatever today's spend is
    with closing(db.connect(data)) as c:
        d = guard.check_step(c, data, VID, "transcribe", cfg)
    assert d.allowed


def test_unreadable_config_fails_the_step_without_spending(data, cfg):
    queue(data)
    cfg.write_text("port = [")
    w, b = worker(data, cfg)
    w.run_next()
    j = job(data)
    assert j["state"] == "failed" and states(data)["transcribe"] == "failed"
    assert "config.toml" in next(s for s in j["steps"] if s["name"] == "transcribe")["message"]
    assert "transcribe" not in b.calls


def test_worker_without_config_path_does_not_check(data):
    queue(data)
    w = Worker(data, build_fakes(FakeBehavior(cost_micro=10_000_000)))
    w.run_next()
    assert job(data)["state"] == "done"


def test_retried_paid_step_is_subject_to_the_same_check(data, cfg):
    set_cap(cfg, 50.0)
    queue(data)
    b = FakeBehavior(fail_at="summarize", cost_micro=1000)
    Worker(data, build_fakes(b), config_path=cfg).run_next()
    assert job(data)["state"] == "failed"
    set_cap(cfg, 0.0001)
    assert client(data, cfg).post(f"/episodes/{VID}/retry").status_code == 303
    Worker(data, build_fakes(FakeBehavior(cost_micro=1000)), config_path=cfg).run_next()
    assert job(data)["state"] == "paused"


def test_library_label_and_recovery_leaves_paused_alone(data, cfg):
    crossing(data, cfg)
    assert "Paused (Daily Cap)" in client(data, cfg).get("/").text
    from app.core.recovery import recover
    with closing(db.connect(data)) as c:
        assert recover(c, data) == 0
    assert job(data)["state"] == "paused"


def test_hostile_reason_escaped(data, cfg):
    crossing(data, cfg)
    with closing(db.connect(data)) as c:
        c.execute("UPDATE jobs SET pause_reason = '<script>x</script>'")
    assert "<script>x</script>" not in client(data, cfg).get(f"/episodes/{VID}").text


def test_resume_is_post_only(data, cfg):
    crossing(data, cfg)
    assert client(data, cfg).get(f"/episodes/{VID}/resume").status_code in (404, 405)
    assert job(data)["state"] == "paused"
