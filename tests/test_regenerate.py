from contextlib import closing
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.adapters.fakes import FakeBehavior, build_fakes
from app.config import PROJECT_ROOT, load_config
from app.core import submit
from app.core.estimate import Prices, estimate_regeneration
from app.core.regenerate import change_summary, history
from app.ports import OnePager, Section
from app.store import artifacts, db, episodes
from app.web.app import create_app
from app.worker import Worker

VID = "dQw4w9WgXcQ"
VID2 = "aaaaaaaaaaa"
FAKE_CONFIG = Path(__file__).parent / "fake_config.toml"


@pytest.fixture
def data(tmp_path):
    db.bootstrap(tmp_path)
    return tmp_path


@pytest.fixture
def real_cfg(tmp_path):
    p = tmp_path / "real.toml"
    p.write_text((PROJECT_ROOT / "config.toml").read_text())
    return p


def client(data, cfg=FAKE_CONFIG):
    return TestClient(create_app([], data, config_path=cfg))


def enqueue(data, vid=VID):
    with closing(db.connect(data)) as c:
        submit.submit(c, f"https://www.youtube.com/watch?v={vid}")


def run(data, b=None):
    return Worker(data, build_fakes(b)).run_next()


def jobs(data, vid=VID):
    with closing(db.connect(data)) as c:
        return c.execute("SELECT id, state FROM jobs WHERE video_id = ? ORDER BY id",
                         (vid,)).fetchall()


def steps(data, job_id):
    with closing(db.connect(data)) as c:
        return {s["name"]: s["state"] for s in episodes.get_steps(c, job_id)}


def versions(data, vid=VID):
    with closing(db.connect(data)) as c:
        return episodes.list_one_pager_versions(c, vid)


def spend_total(data):
    with closing(db.connect(data)) as c:
        return c.execute("SELECT COALESCE(SUM(amount_micro_usd),0) FROM spend_ledger").fetchone()[0]


def done_episode(data, vid=VID):
    enqueue(data, vid)
    assert run(data)


def regen(c, data=None, vid=VID):
    page = c.get(f"/episodes/{vid}/regenerate")
    assert page.status_code == 200
    import re
    est = re.search(r'name="estimate" value="(\d+)"', page.text).group(1)
    return c.post(f"/episodes/{vid}/regenerate", content=f"estimate={est}",
                  headers={"content-type": "application/x-www-form-urlencoded"},
                  follow_redirects=False)


def post(c, est, vid=VID):
    return c.post(f"/episodes/{vid}/regenerate", content=f"estimate={est}",
                  headers={"content-type": "application/x-www-form-urlencoded"},
                  follow_redirects=False)


# ---- estimator

def prices(**kw):
    base = dict(summarizer_input_usd_per_million=2.0, summarizer_output_usd_per_million=10.0,
                verifier_input_usd_per_million=2.0, verifier_output_usd_per_million=10.0,
                token_limit=150000, map_chunk_tokens=60000, map_output_tokens=4096,
                anthropic_max_output_tokens=4096, openai_max_output_tokens=16000,
                verifier_provider="openai")
    base.update(kw)
    return Prices(**base)


def test_estimate_single_call_exact():
    e = estimate_regeneration(100000, 2000, prices())
    assert e.summarize_micro == (101500 * 2 + 4096 * 10)
    ideas = 101000 * 2 + 2000 * 10
    claims = (100000 + 2000 + 1000) * 2 + 8000 * 10
    cover = (2000 + 2000) * 2 + 1500 * 10
    assert e.verify_micro == ideas + claims + cover
    assert e.total_micro == e.summarize_micro + e.verify_micro
    assert e.assumptions["summarize"]["mode"] == "single call"


def test_estimate_map_reduce():
    e = estimate_regeneration(200000, 2000, prices())
    chunks = 4  # ceil(200000 / 60000)
    mapc = (200000 + 1500 * chunks) * 2 + chunks * 4096 * 10
    reduce = (chunks * 4096 + 1500) * 2 + 4096 * 10
    assert e.summarize_micro == mapc + reduce
    assert e.assumptions["summarize"]["chunks"] == chunks
    # chunk size is capped by token_limit
    e2 = estimate_regeneration(30001, 0, prices(token_limit=30000, map_chunk_tokens=60000))
    assert e2.assumptions["summarize"]["chunks"] == 2


def test_estimate_fake_and_none_verifier_are_free():
    for name in ("fake", "none"):
        e = estimate_regeneration(1000, 100, prices(verifier_provider=name))
        assert e.verify_micro == 0 and e.total_micro == e.summarize_micro


# ---- summaries

def v(n, hashes, model="m"):
    return {"version": n, "model": model, "prompt_hashes": hashes}


def test_change_summaries():
    a = {"summarize": "1", "x": "1"}
    assert change_summary(None, v(1, a)) == "first version"
    assert change_summary(v(1, a), v(2, dict(a))) == "no prompt change"
    assert change_summary(v(1, a), v(2, {**a, "summarize": "2"})) == "changed: summarize"
    assert change_summary(v(1, a), v(2, {**a, "summarize_map": "1"})) == "added: summarize_map"
    assert change_summary(v(1, a), v(2, {"summarize": "1"})) == "removed: x"
    assert change_summary(v(1, a), v(2, a, "n")) == "model changed: m to n"
    assert change_summary(v(1, a), v(2, {})) == "no prompt record"
    rows = history([v(1, a), v(2, a), v(3, a)])
    assert [r["version"] for r in rows] == [3, 2, 1]


# ---- confirmation page and queueing

def test_prompt_edit_queues_nothing(data, tmp_path):
    done_episode(data)
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    (prompts / "summarize.md").write_text("A: one\n---\nOriginal.")
    before, spent = jobs(data), spend_total(data)
    (prompts / "summarize.md").write_text("A: one\nB: two\n---\nEdited.")   # the edit
    c = client(data)
    assert c.get(f"/episodes/{VID}").status_code == 200      # viewing pages queues nothing
    assert c.get(f"/episodes/{VID}/regenerate").status_code == 200
    assert jobs(data) == before and len(before) == 1 and spend_total(data) == spent


def test_confirmation_page_estimates_and_queues_nothing(data, real_cfg):
    done_episode(data)
    c = client(data, real_cfg)
    r = c.get(f"/episodes/{VID}/regenerate")
    assert r.status_code == 200
    for needle in ("Summarize:", "Verify:", "Total:", "Today: $0.00 of $5.00; after this about",
                   "estimate", "Confirm"):
        assert needle in r.text
    assert len(jobs(data)) == 1


def test_fake_verifier_says_free(data):
    done_episode(data)
    t = client(data).get(f"/episodes/{VID}/regenerate").text
    assert "costs nothing" in t and "$0.0000" in t


def test_confirm_queues_one_job_and_worker_runs_only_two_steps(data):
    done_episode(data)
    c = client(data)
    r = regen(c)
    assert r.status_code == 303 and r.headers["location"] == f"/episodes/{VID}"
    js = jobs(data)
    assert len(js) == 2 and js[1][1] == "queued"
    assert steps(data, js[1][0]) == {"download": "done", "transcribe": "done",
                                     "summarize": "pending", "verify": "pending"}
    old = {n: (artifacts.artifact_path(data, VID, n).read_bytes())
           for n in ("one_pager.v1.json", "verification.v1.json")}
    b = FakeBehavior()
    assert run(data, b)
    assert b.calls == ["summarize", "verify"]
    assert [x["version"] for x in versions(data)] == [1, 2]
    assert artifacts.exists(artifacts.one_pager_path(data, VID, 2))
    assert artifacts.exists(artifacts.verification_path(data, VID, 2))
    for n, content in old.items():
        assert artifacts.artifact_path(data, VID, n).read_bytes() == content
    assert jobs(data)[1][1] == "done"


def test_stale_estimate_starts_nothing(data):
    done_episode(data)
    r = post(client(data), 1)
    assert r.status_code == 200 and "changed" in r.text and "Confirm" in r.text
    assert len(jobs(data)) == 1
    assert post(client(data), "abc").status_code == 200
    assert len(jobs(data)) == 1


def test_stale_when_config_price_changes(data, real_cfg):
    done_episode(data)
    c = client(data, real_cfg)
    page = c.get(f"/episodes/{VID}/regenerate").text
    import re
    est = re.search(r'name="estimate" value="(\d+)"', page).group(1)
    real_cfg.write_text(real_cfg.read_text().replace(
        "summarizer_input_usd_per_million = 2.0", "summarizer_input_usd_per_million = 3.0"))
    r = post(c, est)
    assert r.status_code == 200 and "changed" in r.text and len(jobs(data)) == 1


def test_double_submit_one_job(data):
    done_episode(data)
    c = client(data)
    page = c.get(f"/episodes/{VID}/regenerate").text
    import re
    est = re.search(r'name="estimate" value="(\d+)"', page).group(1)
    assert post(c, est).status_code == 303
    r = post(c, est)
    assert r.status_code == 409 and r.text
    assert len(jobs(data)) == 2


def test_store_enqueue_guard(data):
    done_episode(data)
    with closing(db.connect(data)) as c:
        first = episodes.enqueue_regeneration(c, VID, submit.STEP_NAMES, ("download", "transcribe"))
        second = episodes.enqueue_regeneration(c, VID, submit.STEP_NAMES, ("download", "transcribe"))
    assert first is not None and second is None and len(jobs(data)) == 2


def test_active_job_blocks_and_hides_link(data):
    enqueue(data)  # queued, not run
    c = client(data)
    assert c.get(f"/episodes/{VID}/regenerate").status_code == 409
    assert post(c, 1).status_code == 409
    assert "/regenerate" not in c.get(f"/episodes/{VID}").text
    assert len(jobs(data)) == 1


def test_no_transcript_blocks(data):
    done_episode(data)
    artifacts.artifact_path(data, VID, artifacts.TRANSCRIPT).unlink()
    c = client(data)
    r = c.get(f"/episodes/{VID}/regenerate")
    assert r.status_code == 409 and "Transcript" in r.text
    assert post(c, 1).status_code == 409
    assert "/regenerate" not in c.get(f"/episodes/{VID}").text
    assert len(jobs(data)) == 1


def test_link_shown_when_possible(data):
    done_episode(data)
    assert f'href="/episodes/{VID}/regenerate"' in client(data).get(f"/episodes/{VID}").text


def test_unknown_episode_404(data):
    c = client(data)
    assert c.get("/episodes/zzzzzzzzzzz/regenerate").status_code == 404
    assert post(c, 1, "zzzzzzzzzzz").status_code == 404


def test_queue_order(data):
    done_episode(data)
    enqueue(data, VID2)
    assert regen(client(data)).status_code == 303
    b = FakeBehavior()
    w = Worker(data, build_fakes(b))
    assert w.run_next()  # VID2's first job is older
    assert b.calls == ["download", "transcribe", "summarize", "verify"]
    assert jobs(data)[1][1] == "queued"
    assert w.run_next()
    assert jobs(data)[1][1] == "done"


def test_summarize_failure_keeps_previous(data):
    done_episode(data)
    assert regen(client(data)).status_code == 303
    assert run(data, FakeBehavior(fail_at="summarize", message="boom"))
    js = jobs(data)
    assert js[1][1] == "failed"
    assert [x["version"] for x in versions(data)] == [1]
    t = client(data).get(f"/episodes/{VID}").text
    assert "boom" in t and "One-Pager (v1)" in t


def test_unreadable_config_message(data, tmp_path):
    done_episode(data)
    bad = tmp_path / "bad.toml"
    bad.write_text("not [valid")
    c = client(data, bad)
    r = c.get(f"/episodes/{VID}/regenerate")
    assert r.status_code == 200 and "config" in r.text and "Confirm" not in r.text
    assert post(c, 1).status_code == 200
    assert len(jobs(data)) == 1


# ---- history and version pages

def test_history_and_old_version(data):
    done_episode(data)
    c = client(data)
    regen(c)
    run(data)
    regen(c)
    run(data)
    t = c.get(f"/episodes/{VID}").text
    assert t.index("/versions/3") < t.index("/versions/2") < t.index("/versions/1")
    assert "first version" in t and "no prompt record" in t
    r = c.get(f"/episodes/{VID}/versions/1")
    assert r.status_code == 200 and "earlier version" in r.text and "Accuracy" in r.text
    assert "earlier version" not in c.get(f"/episodes/{VID}/versions/3").text
    assert c.get(f"/episodes/{VID}/versions/1").text.count("Back to episode") == 1


@pytest.mark.parametrize("n", ["abc", "99", "0", "-1", "01", "1.0", "%201"])
def test_bad_version_404(data, n):
    done_episode(data)
    assert client(data).get(f"/episodes/{VID}/versions/{n}").status_code == 404


def test_unreadable_version_file_is_notice(data):
    done_episode(data)
    artifacts.one_pager_path(data, VID, 1).write_text("{broken")
    r = client(data).get(f"/episodes/{VID}/versions/1")
    assert r.status_code == 200 and "could not be read" in r.text
    assert client(data).get(f"/episodes/{VID}").status_code == 200


def test_hostile_text_escaped(data):
    done_episode(data)
    with closing(db.connect(data)) as c:
        episodes.add_one_pager_version(c, VID, 2, "<script>x</script>", {"a": "1"})
        c.commit()
    artifacts.write_json(artifacts.one_pager_path(data, VID, 2), OnePager(
        (Section("<b>S</b>", "<script>alert(1)</script>"),)).to_dict())
    cl = client(data)
    for url in (f"/episodes/{VID}", f"/episodes/{VID}/versions/2"):
        t = cl.get(url).text
        assert "<script>" not in t.replace('<script src="/static/htmx.min.js">', "")
        assert "&lt;script&gt;" in t


# --- review patches ---

def _set_job_state(data, state):
    with closing(db.connect(data)) as c:
        job = episodes.get_latest_job_with_steps(c, VID)
        episodes.set_job_state(c, job["id"], state)


@pytest.mark.parametrize("state", ["queued", "running", "paused"])
def test_every_active_state_blocks_regeneration(data, state):
    done_episode(data)
    _set_job_state(data, state)
    c = client(data)
    assert c.get(f"/episodes/{VID}/regenerate").status_code == 409
    assert post(c, 1).status_code == 409
    assert "/regenerate" not in c.get(f"/episodes/{VID}").text
    with closing(db.connect(data)) as conn:
        assert episodes.has_active_job(conn, VID)
        assert episodes.enqueue_regeneration(conn, VID, ("download", "transcribe", "summarize", "verify"),
                                             ("download", "transcribe")) is None
    assert len(jobs(data)) == 1


def test_page_estimate_equals_the_estimator_on_the_stored_artifacts(data, real_cfg):
    from app.core.chunking import estimate_tokens
    from app.core.text import transcript_text
    from app.ports import Transcript
    done_episode(data)
    t = Transcript.from_dict(artifacts.read_json(artifacts.artifact_path(data, VID, "transcript.json")))
    page = OnePager.from_dict(artifacts.read_json(artifacts.one_pager_path(data, VID, 1)))
    chars = len("\n\n".join(f"## {s.name}\n{s.text}" for s in page.sections))
    expected = estimate_regeneration(estimate_tokens(transcript_text(t)),
                                     estimate_tokens("x" * chars),
                                     Prices.from_config(load_config(real_cfg)))
    html = client(data, real_cfg).get(f"/episodes/{VID}/regenerate").text
    assert f'name="estimate" value="{expected.total_micro}"' in html
    # a bigger transcript gives a bigger estimate
    long = artifacts.read_json(artifacts.artifact_path(data, VID, "transcript.json"))
    long["segments"] = long["segments"] * 400
    artifacts.write_json(artifacts.artifact_path(data, VID, "transcript.json"), long)
    html2 = client(data, real_cfg).get(f"/episodes/{VID}/regenerate").text
    import re
    assert int(re.search(r'name="estimate" value="(\d+)"', html2).group(1)) > expected.total_micro


def test_corrupt_transcript_blocks_with_409_on_get_and_post(data):
    done_episode(data)
    artifacts.artifact_path(data, VID, "transcript.json").write_text("{broken")
    c = client(data)
    assert c.get(f"/episodes/{VID}/regenerate").status_code == 409
    assert post(c, 1).status_code == 409
    assert len(jobs(data)) == 1


def test_empty_transcript_blocks(data):
    done_episode(data)
    artifacts.write_json(artifacts.artifact_path(data, VID, "transcript.json"), {"segments": []})
    assert client(data).get(f"/episodes/{VID}/regenerate").status_code == 409


def test_corrupt_one_pager_still_gives_an_estimate(data, real_cfg):
    done_episode(data)
    artifacts.one_pager_path(data, VID, 1).write_text("{broken")
    r = client(data, real_cfg).get(f"/episodes/{VID}/regenerate")
    assert r.status_code == 200 and 'name="estimate"' in r.text


@pytest.mark.parametrize("bad", ["[pricing]\nsummarizer_input_usd_per_million = \"x\"\n", "not toml ["])
def test_unusable_config_gives_a_message_and_starts_nothing(data, tmp_path, bad):
    done_episode(data)
    cfg = tmp_path / "bad.toml"
    cfg.write_text(bad)
    c = client(data, cfg)
    r = c.get(f"/episodes/{VID}/regenerate")
    assert r.status_code == 200 and "config file could not be read" in r.text
    assert 'name="estimate"' not in r.text
    r = post(c, 1)
    assert r.status_code == 200 and len(jobs(data)) == 1


def test_stale_post_shows_the_new_figure_not_the_old_one(data, real_cfg):
    import re
    done_episode(data)
    c = client(data, real_cfg)
    first = re.search(r'value="(\d+)"', c.get(f"/episodes/{VID}/regenerate").text).group(1)
    real_cfg.write_text(real_cfg.read_text().replace("summarizer_output_usd_per_million = 10.0",
                                                     "summarizer_output_usd_per_million = 20.0"))
    r = post(c, first)
    assert r.status_code == 200 and "estimate changed" in r.text and len(jobs(data)) == 1
    new = re.search(r'value="(\d+)"', r.text).group(1)
    assert int(new) > int(first)
    assert post(c, new).status_code == 303 and len(jobs(data)) == 2


def test_confirmation_page_warns_when_over_the_daily_cap(data, real_cfg):
    done_episode(data)
    real_cfg.write_text(real_cfg.read_text().replace("daily_cap_usd = 5.0", "daily_cap_usd = 0.01"))
    html = client(data, real_cfg).get(f"/episodes/{VID}/regenerate").text
    assert 'id="over-cap"' in html and "will be refused" in html
    real_cfg.write_text(real_cfg.read_text().replace("daily_cap_usd = 0.01", "daily_cap_usd = 50.0"))
    assert 'id="over-cap"' not in client(data, real_cfg).get(f"/episodes/{VID}/regenerate").text


def test_version_page_returns_503_when_the_database_fails(data, monkeypatch):
    import sqlite3
    done_episode(data)

    def boom(*a, **k):
        raise sqlite3.OperationalError("locked")

    monkeypatch.setattr(episodes, "list_one_pager_versions", boom)
    assert client(data).get(f"/episodes/{VID}/versions/1").status_code == 503


def test_version_page_survives_a_corrupt_check_file(data):
    done_episode(data)
    artifacts.verification_path(data, VID, 1).write_text("{broken")
    r = client(data).get(f"/episodes/{VID}/versions/1")
    assert r.status_code == 200 and "Accuracy" not in r.text


@pytest.mark.parametrize("raw", ["garbage", "[]", "null", '"x"'])
def test_malformed_stored_hashes_do_not_break_the_page(data, raw):
    done_episode(data)
    c = client(data)
    regen(c)
    run(data)
    with closing(db.connect(data)) as conn:
        conn.execute("UPDATE one_pager_versions SET prompt_hashes = ? WHERE version = 1", (raw,))
    r = c.get(f"/episodes/{VID}")
    assert r.status_code == 200 and "no prompt record" in r.text


def test_summary_when_a_record_is_missing_on_either_side():
    old = {"model": "m", "prompt_hashes": {}}
    new = {"model": "m", "prompt_hashes": {"summarize": "a"}}
    assert change_summary(old, new) == "no prompt record"      # not "added: summarize"
    assert change_summary(new, old) == "no prompt record"
    assert change_summary(old, {"model": "n", "prompt_hashes": {}}) == \
        "no prompt record; model changed: m to n"


def test_history_marks_the_latest_version(data):
    done_episode(data)
    c = client(data)
    regen(c)
    run(data)
    t = c.get(f"/episodes/{VID}").text
    assert t.count("(latest)") == 1 and t.index("/versions/2") < t.index("(latest)") < t.index("/versions/1")


def test_failed_regeneration_does_not_block_the_next_one(data):
    done_episode(data)
    c = client(data)
    regen(c)
    assert run(data, FakeBehavior(fail_at="summarize"))
    assert [s for _, s in jobs(data)] == ["done", "failed"] and len(versions(data)) == 1
    assert c.get(f"/episodes/{VID}/regenerate").status_code == 200
    assert regen(c).status_code == 303 and run(data) and len(versions(data)) == 2
