import ast
import time
from contextlib import closing
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.adapters.fakes import FakeBehavior, build_fakes
from app.config import PROJECT_ROOT, load_config
from app.core import submit
from app.store import artifacts, db, episodes
from app.web.app import create_app
from app.worker import Worker, build_adapters

VID = "dQw4w9WgXcQ"
VID2 = "aaaaaaaaaaa"
FAKE_CONFIG = Path(__file__).parent / "fake_config.toml"


def real_text():
    return (PROJECT_ROOT / "config.toml").read_text()


STEPS = ("download", "transcribe", "summarize", "verify")


@pytest.fixture
def data(tmp_path):
    db.bootstrap(tmp_path)
    return tmp_path


def enqueue(data, vid=VID):
    with closing(db.connect(data)) as c:
        submit.submit(c, f"https://www.youtube.com/watch?v={vid}")


def worker(data, behavior=None, verifier=True, secrets=lambda: ()):
    return Worker(data, build_fakes(behavior, verifier), secrets=secrets)


def job(data, vid=VID):
    with closing(db.connect(data)) as c:
        return episodes.get_latest_job_with_steps(c, vid)


def states(j):
    return {s["name"]: s["state"] for s in j["steps"]}


def files(data, vid=VID):
    d = artifacts.episode_dir(data, vid)
    return sorted(p.name for p in d.iterdir()) if d.exists() else []


def test_happy_path(data):
    enqueue(data)
    assert worker(data).run_next() is True
    j = job(data)
    assert j["state"] == "done"
    assert states(j) == {s: "done" for s in STEPS}
    assert files(data) == sorted(["audio.m4a", "transcript.json", "one_pager.v1.json",
                                  "verification.v1.json"])
    with closing(db.connect(data)) as c:
        ep = episodes.get_episode(c, VID)
    assert ep["title"] == f"Fake episode {VID}" and ep["duration_seconds"] == 3600
    assert worker(data).run_next() is False


def test_serial_oldest_first(data):
    enqueue(data, VID)
    enqueue(data, VID2)
    b = FakeBehavior()
    w = worker(data, b)
    assert w.run_next() and job(data, VID)["state"] == "done"
    assert job(data, VID2)["state"] == "queued"
    assert w.run_next() and job(data, VID2)["state"] == "done"
    assert b.calls == list(STEPS) * 2


def test_skip_finished_steps(data):
    enqueue(data)
    worker(data).run_next()
    with closing(db.connect(data)) as c:
        episodes.set_job_state(c, job(data)["id"], "queued")
    assert job(data)["state"] == "queued"
    b = FakeBehavior()
    worker(data, b).run_next()
    assert b.calls == []
    assert job(data)["state"] == "done"


def test_done_but_file_missing_reruns(data):
    enqueue(data)
    worker(data).run_next()
    artifacts.artifact_path(data, VID, "transcript.json").unlink()
    with closing(db.connect(data)) as c:
        episodes.set_job_state(c, job(data)["id"], "queued")
    b = FakeBehavior()
    worker(data, b).run_next()
    assert artifacts.artifact_path(data, VID, "transcript.json").exists()
    # later steps must re-run too, so they never keep output made from the old transcript
    assert b.calls == ["transcribe", "summarize", "verify"]


@pytest.mark.parametrize("retryable", [True, False])
def test_expected_failure(data, retryable):
    enqueue(data)
    worker(data, FakeBehavior(fail_at="transcribe", retryable=retryable)).run_next()
    j = job(data)
    assert j["state"] == "failed"
    assert states(j) == {"download": "done", "transcribe": "failed",
                         "summarize": "pending", "verify": "pending"}
    t = j["steps"][1]
    assert t["message"] == "fake failure" and t["retryable"] == int(retryable)


def test_unexpected_exception_leaks_nothing(data):
    enqueue(data)
    worker(data, FakeBehavior(unexpected_at="summarize",
                              unexpected_message="secret transcript text")).run_next()
    s = job(data)["steps"][2]
    assert s["state"] == "failed" and s["message"] == "ValueError" and s["retryable"] == 1


def test_secret_redacted_and_truncated(data):
    enqueue(data)
    msg = "bad key sk-SECRET123 " + "x" * 1000
    worker(data, FakeBehavior(fail_at="download", message=msg),
           secrets=lambda: ["sk-SECRET123"]).run_next()
    m = job(data)["steps"][0]["message"]
    assert "sk-SECRET123" not in m and len(m) <= 500


def test_partial_write_leaves_nothing(data):
    enqueue(data)
    worker(data, FakeBehavior(partial_write=True)).run_next()
    assert job(data)["steps"][0]["state"] == "failed"
    assert files(data) == []


def test_json_write_failure_cleans_temp(data):
    final = artifacts.artifact_path(data, VID, "x.json")
    with pytest.raises(TypeError):
        artifacts.write_json(final, {"a": object()})
    assert files(data) == []


def test_no_verifier_skips(data):
    enqueue(data)
    worker(data, verifier=False).run_next()
    j = job(data)
    assert j["state"] == "done" and states(j)["verify"] == "skipped"
    assert j["steps"][3]["message"]
    assert "verification.json" not in files(data)


def test_build_adapters_from_config():
    a = build_adapters(load_config(FAKE_CONFIG))
    assert a.verifier is not None


def test_build_adapters_selects_ytdlp_and_rejects_unknown(tmp_path):
    from app.adapters.ytdlp import YtDlpDownloader
    real = load_config()
    assert real.providers["downloader"] == "yt-dlp"
    assert isinstance(build_adapters(real).downloader, YtDlpDownloader)
    bad = tmp_path / "c.toml"
    bad.write_text(real_text().replace('downloader = "yt-dlp"', 'downloader = "nope"'))
    with pytest.raises(ValueError):
        build_adapters(load_config(bad))


def test_layering():
    banned = ("app.adapters", "yt_dlp", "assemblyai", "anthropic", "openai")
    for pkg in ("core", "web", "ports"):
        for f in (PROJECT_ROOT / "app" / pkg).rglob("*.py"):
            for n in ast.walk(ast.parse(f.read_text())):
                mods = []
                if isinstance(n, ast.Import):
                    mods = [a.name for a in n.names]
                elif isinstance(n, ast.ImportFrom):
                    mods = [n.module or ""] + [f"{n.module}.{a.name}" for a in n.names]
                for m in mods:
                    assert not any(m == b or m.startswith(b + ".") for b in banned), (f, m)


def test_thread_runs_submitted_jobs(data):
    client = TestClient(create_app([], data), follow_redirects=False)
    for vid in (VID, VID2):
        r = client.post("/submit", data={"url": f"https://youtu.be/{vid}"})
        assert r.status_code == 303
    w = worker(data)
    w.poll_interval = 0.02
    w.start()
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not all(
            job(data, v)["state"] == "done" for v in (VID, VID2)
        ):
            time.sleep(0.05)
    finally:
        w.stop()
    assert all(job(data, v)["state"] == "done" for v in (VID, VID2))
    assert client.get(f"/episodes/{VID}").status_code == 200


def test_download_that_writes_nothing_fails_with_clear_message(data):
    enqueue(data)
    w = worker(data)
    real = w.adapters.downloader.download

    def writes_nothing(video_id, url, dest, meter):
        result = real(video_id, url, dest, meter)
        dest.unlink()
        return result

    w.adapters.downloader.download = writes_nothing
    w.run_next()
    step = next(s for s in job(data)["steps"] if s["name"] == "download")
    assert step["state"] == "failed" and "no audio file" in step["message"]
    assert files(data) == []


def test_unsafe_video_id_rejected_by_artifact_paths(tmp_path):
    for bad in ("../x", "a/b", "", ".."):
        with pytest.raises(ValueError):
            artifacts.episode_dir(tmp_path, bad)
