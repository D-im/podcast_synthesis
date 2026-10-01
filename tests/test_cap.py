import re
from contextlib import closing
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.adapters.fakes import FakeBehavior, build_fakes
from app.adapters.ytdlp import YtDlpDownloader
from app.config import PROJECT_ROOT, load_config
from app.core import submit
from app.core.budget import check_budget
from app.core.estimate import Prices, estimate_job, estimate_regeneration
from app.ports import DownloadResult, StepError
from app.store import db, episodes
from app.web.app import create_app
from app.worker import Worker

VID = "dQw4w9WgXcQ"
FORM = {"content-type": "application/x-www-form-urlencoded"}
URL = f"url=https://www.youtube.com/watch?v={VID}"


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


def fake_lookup(seconds=3600, calls=None):
    def lookup(video_id, url):
        if calls is not None:
            calls.append(video_id)
        return DownloadResult(f"Title {video_id}", seconds)
    return lookup


def client(data, cfg, lookup):
    return TestClient(create_app([], data, config_path=cfg, lookup=lookup),
                      follow_redirects=False)


def counts(data):
    with closing(db.connect(data)) as c:
        return tuple(c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                     for t in ("episodes", "jobs", "steps"))


def add_spend(data, micro):
    with closing(db.connect(data)) as c:
        c.execute("INSERT INTO episodes (video_id, url, created_at) VALUES ('old', 'u', 't')")
        from app.core.meter import StepMeter
        StepMeter(c, "old", "summarize").record("anthropic", micro)


def prices(**kw):
    base = dict(summarizer_input_usd_per_million=2.0, summarizer_output_usd_per_million=10.0,
                verifier_input_usd_per_million=2.0, verifier_output_usd_per_million=10.0,
                token_limit=150000, map_chunk_tokens=60000, map_output_tokens=4096,
                anthropic_max_output_tokens=4096, openai_max_output_tokens=16000,
                verifier_provider="openai", transcription_usd_per_hour=0.23,
                transcriber_provider="assemblyai")
    base.update(kw)
    return Prices(**base)


# ---- estimator ---------------------------------------------------------------------------

def test_estimate_job_adds_transcription_from_duration():
    est = estimate_job(7200, prices())
    assert est.transcribe_micro == 460000          # 2 h at $0.23
    tokens = -(-7200 * 18 // 3)
    rest = estimate_regeneration(tokens, 4096, prices())
    assert est.summarize_micro == rest.summarize_micro and est.verify_micro == rest.verify_micro
    assert est.total_micro == 460000 + rest.total_micro


def test_estimate_job_fake_transcriber_costs_nothing_and_zero_duration():
    assert estimate_job(3600, prices(transcriber_provider="fake")).transcribe_micro == 0
    assert estimate_job(0, prices()).transcribe_micro == 0
    assert estimate_job(1800, prices()).transcribe_micro == 115000


def test_estimate_job_long_video_uses_map_reduce():
    est = estimate_job(20 * 3600, prices())
    assert est.assumptions["summarize"]["mode"] == "map-reduce"


# ---- budget decision ---------------------------------------------------------------------

@pytest.mark.parametrize("today,est,cap,ok", [
    (0, 100, 1000, True), (900, 100, 1000, True), (901, 100, 1000, False),
    (1000, 0, 1000, False), (1200, 0, 1000, False), (0, 1, 0, False), (0, 0, 1, True),
])
def test_check_budget(today, est, cap, ok):
    d = check_budget(today, est, cap)
    assert d.allowed is ok and (d.reason is None) is ok


# ---- submissions -------------------------------------------------------------------------

def test_within_cap_creates_episode_with_estimate_and_shows_it(data, cfg):
    calls = []
    c = client(data, cfg, fake_lookup(calls=calls))
    r = c.post("/submit", content=URL, headers=FORM)
    assert r.status_code == 303 and calls == [VID]
    with closing(db.connect(data)) as conn:
        ep = episodes.get_episode(conn, VID)
        job = episodes.get_latest_job_with_steps(conn, VID)
    assert ep["title"] == f"Title {VID}" and ep["duration_seconds"] == 3600
    assert job["estimate_micro"] and job["estimate_micro"] > 230000     # at least transcription
    page = c.get(f"/episodes/{VID}").text
    assert "estimate <strong>$" in page


def test_over_cap_refused_with_reason_and_nothing_left(data, cfg):
    set_cap(cfg, 0.10)
    r = client(data, cfg, fake_lookup()).post("/submit", content=URL, headers=FORM)
    assert r.status_code == 409
    assert "Refused" in r.text and "$0.10" in r.text and "estimate" in r.text
    assert "Nothing was created" in r.text and "would go over the Daily Cap" in r.text
    assert counts(data) == (0, 0, 0)


def test_spend_already_at_cap_refuses_anything(data, cfg):
    set_cap(cfg, 1.0)
    add_spend(data, 1_000_000)
    r = client(data, cfg, fake_lookup(seconds=1)).post("/submit", content=URL, headers=FORM)
    assert r.status_code == 409 and "already reached" in r.text
    assert counts(data) == (1, 0, 0)               # only the seeded episode


def test_cap_change_applies_to_next_submission(data, cfg):
    c = client(data, cfg, fake_lookup())
    set_cap(cfg, 0.10)
    assert c.post("/submit", content=URL, headers=FORM).status_code == 409
    set_cap(cfg, 5.0)
    assert c.post("/submit", content=URL, headers=FORM).status_code == 303


def test_default_cap_is_five_dollars(tmp_path):
    base = (PROJECT_ROOT / "config.toml").read_text()
    assert load_config(PROJECT_ROOT / "config.toml").daily_cap_usd == 5.0 and "daily_cap_usd = 5.0" in base


def test_existing_episode_resubmit_skips_lookup(data, cfg):
    calls = []
    c = client(data, cfg, fake_lookup(calls=calls))
    c.post("/submit", content=URL, headers=FORM)
    set_cap(cfg, 0.01)
    r = c.post("/submit", content=URL, headers=FORM)
    assert r.status_code == 303 and calls == [VID]


@pytest.mark.parametrize("error", [StepError("video is private", False), RuntimeError("x")])
def test_lookup_failure_refuses_and_creates_nothing(data, cfg, error):
    def bad(video_id, url):
        raise error
    r = client(data, cfg, bad).post("/submit", content=URL, headers=FORM)
    assert r.status_code == 409 and "Nothing was created" in r.text
    assert counts(data) == (0, 0, 0)


def test_unreadable_config_refuses(data, cfg):
    cfg.write_text("port = [")
    r = client(data, cfg, fake_lookup()).post("/submit", content=URL, headers=FORM)
    assert r.status_code == 409 and "config file" in r.text and counts(data) == (0, 0, 0)


def test_invalid_url_still_400_and_no_lookup(data, cfg):
    calls = []
    r = client(data, cfg, fake_lookup(calls=calls)).post("/submit", content="url=nope", headers=FORM)
    assert r.status_code == 400 and calls == []


def test_without_lookup_submission_unchanged(data, cfg):
    c = TestClient(create_app([], data, config_path=cfg), follow_redirects=False)
    assert c.post("/submit", content=URL, headers=FORM).status_code == 303


def test_hostile_title_escaped(data, cfg):
    def lookup(video_id, url):
        return DownloadResult("<script>x</script>", 60)
    c = client(data, cfg, lookup)
    c.post("/submit", content=URL, headers=FORM)
    page = c.get(f"/episodes/{VID}").text
    assert "<script>x</script>" not in page


def test_fake_adapters_lookup_and_estimate_vs_actual_after_a_run(data, cfg):
    fakes = build_fakes(FakeBehavior(cost_micro=1000))
    c = client(data, cfg, fakes.downloader.lookup)
    c.post("/submit", content=URL, headers=FORM)
    assert Worker(data, fakes).run_next()
    page = c.get(f"/episodes/{VID}").text
    assert "estimate <strong>$" in page and "actual so far <strong>$0.0040" in page


# ---- regeneration ------------------------------------------------------------------------

def done_episode(data):
    with closing(db.connect(data)) as conn:
        submit.submit(conn, f"https://www.youtube.com/watch?v={VID}")
    assert Worker(data, build_fakes()).run_next()


def regen_post(c, est):
    return c.post(f"/episodes/{VID}/regenerate", content=f"estimate={est}", headers=FORM)


def estimate_of(c):
    return re.search(r'name="estimate" value="(\d+)"', c.get(f"/episodes/{VID}/regenerate").text).group(1)


def test_regeneration_over_cap_refused_same_way(data, cfg):
    done_episode(data)
    c = client(data, cfg, None)
    est = estimate_of(c)
    set_cap(cfg, 0.01)
    # the estimate does not depend on the cap, so it still matches and the cap decides
    r = regen_post(c, est)
    assert r.status_code == 409 and "Refused" in r.text and "Nothing was started" in r.text
    assert "Cap $0.01" in r.text and "today&#39;s spend" in r.text and "estimate $" in r.text
    with closing(db.connect(data)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1


def test_regeneration_within_cap_queues_with_estimate(data, cfg):
    done_episode(data)
    c = client(data, cfg, None)
    est = estimate_of(c)
    assert regen_post(c, est).status_code == 303
    with closing(db.connect(data)) as conn:
        job = episodes.get_latest_job_with_steps(conn, VID)
    assert job["state"] == "queued" and job["estimate_micro"] == int(est)


def test_regeneration_refused_when_spend_reached_cap(data, cfg):
    done_episode(data)
    c = client(data, cfg, None)
    est = estimate_of(c)
    set_cap(cfg, 1.0)
    add_spend(data, 1_000_000)
    assert regen_post(c, est).status_code == 409


# ---- yt-dlp lookup -----------------------------------------------------------------------

class FakeYdl:
    def __init__(self, info=None, error=None):
        self.info, self.error, self.downloaded = info, error, None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def extract_info(self, url, download=True):
        self.downloaded = download
        if self.error:
            raise self.error
        return self.info


def ytdlp(info=None, error=None):
    ydl = FakeYdl(info, error)
    return YtDlpDownloader("node", ydl_factory=lambda opts: ydl), ydl


def test_ytdlp_lookup_returns_title_duration_without_downloading():
    d, ydl = ytdlp({"title": "  A   title ", "duration": 125.4})
    r = d.lookup(VID, "u")
    assert (r.title, r.duration_seconds) == ("A title", 125) and ydl.downloaded is False


@pytest.mark.parametrize("info", [None, {"duration": None}, {"duration": 0}, {"duration": True},
                                  {"duration": 10, "is_live": True},
                                  {"duration": 10, "live_status": "is_upcoming"}])
def test_ytdlp_lookup_rejects_unusable_info(info):
    d, _ = ytdlp(info)
    with pytest.raises(StepError):
        d.lookup(VID, "u")


def test_ytdlp_lookup_maps_network_errors():
    d, _ = ytdlp(error=ConnectionError("x"))
    with pytest.raises(StepError):
        d.lookup(VID, "u")
