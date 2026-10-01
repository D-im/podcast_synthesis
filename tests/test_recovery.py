from contextlib import closing
from pathlib import Path

import pytest

from app.adapters import anthropic as ant
from app.adapters import assemblyai as aai
from app.adapters import openai as oa
from app.adapters.fakes import FakeBehavior, build_fakes
from app.config import ConfigError, load_config
from app.core import submit
from app.core.recovery import recover
from app.core.retry import Retry, call_with_backoff
from app.ports import StepError
from app.store import artifacts, db, episodes
from app.worker import Worker

from tests import test_anthropic as ta
from tests import test_assemblyai as tas
from tests import test_openai as to

VID = "dQw4w9WgXcQ"
ROOT = Path(__file__).resolve().parent


@pytest.fixture
def data(tmp_path):
    db.bootstrap(tmp_path)
    return tmp_path


def queue(data, vid=VID):
    with closing(db.connect(data)) as c:
        return submit.submit(c, f"https://www.youtube.com/watch?v={vid}")


def states(data, vid=VID):
    with closing(db.connect(data)) as c:
        job = episodes.get_latest_job_with_steps(c, vid)
    return job["state"], {s["name"]: s["state"] for s in job["steps"]}


def ledger(data):
    with closing(db.connect(data)) as c:
        return c.execute("SELECT step, provider, amount_micro_usd, provider_ref "
                         "FROM spend_ledger ORDER BY id").fetchall()


def do_recover(data):
    with closing(db.connect(data)) as c:
        return recover(c, data)


class Killed(BaseException):
    """Stands in for a kill: not an Exception, so the pipeline cannot record a failure."""


def killing(step, **kw):
    """Fakes whose adapter for `step` dies (after charging, if a cost is set) on first use."""
    b = FakeBehavior(**kw)
    adapters = build_fakes(b)
    target = {"transcribe": adapters.transcriber, "summarize": adapters.summarizer,
              "verify": adapters.verifier}[step]
    method = {"transcribe": "transcribe", "summarize": "summarize", "verify": "verify"}[step]
    real = getattr(target, method)
    state = {"dead": True}

    def die(*a, **k):
        if state["dead"]:
            state["dead"] = False
            raise Killed()
        return real(*a, **k)
    setattr(target, method, die)
    return adapters, b


# ---- recovery ----------------------------------------------------------------------------

def test_running_job_requeued_and_steps_reset(data):
    queue(data)
    adapters, _ = killing("transcribe")
    with pytest.raises(Killed):
        Worker(data, adapters).run_next()
    assert states(data) == ("running", {"download": "done", "transcribe": "running",
                                        "summarize": "pending", "verify": "pending"})
    assert do_recover(data) == 1
    assert states(data) == ("queued", {"download": "done", "transcribe": "pending",
                                       "summarize": "pending", "verify": "pending"})


def test_temp_files_removed_only_in_recovered_folder(data):
    queue(data)
    adapters, _ = killing("transcribe")
    with pytest.raises(Killed):
        Worker(data, adapters).run_next()
    folder = artifacts.episode_dir(data, VID)
    (folder / "transcript.json.tmp").write_text("x")
    (folder / "audio.mp3.tmp").write_text("x")
    (folder / "sub.tmp").mkdir()
    other = artifacts.episode_dir(data, "aaaaaaaaaaa")
    other.mkdir(parents=True)
    (other / "keep.tmp").write_text("x")
    final = sorted(p.name for p in folder.iterdir() if not p.name.endswith(".tmp"))
    do_recover(data)
    assert sorted(p.name for p in folder.iterdir() if not p.name.endswith(".tmp")) == final
    assert not (folder / "transcript.json.tmp").exists() and not (folder / "audio.mp3.tmp").exists()
    assert (folder / "sub.tmp").is_dir()          # directories are left alone
    assert (other / "keep.tmp").exists()          # other Episodes are left alone


def test_other_states_untouched(data):
    ids = {}
    for i, state in enumerate(["queued", "paused", "failed", "done"]):
        vid = f"{chr(97 + i)}" * 11
        queue(data, vid)
        with closing(db.connect(data)) as c:
            job = episodes.get_latest_job_with_steps(c, vid)
            episodes.set_job_state(c, job["id"], state)
            ids[vid] = state
    assert do_recover(data) == 0
    for vid, state in ids.items():
        assert states(data, vid)[0] == state


def test_nothing_to_do_and_idempotent(data):
    assert do_recover(data) == 0
    queue(data)
    adapters, _ = killing("transcribe")
    with pytest.raises(Killed):
        Worker(data, adapters).run_next()
    assert do_recover(data) == 1
    before = states(data)
    assert do_recover(data) == 0 and states(data) == before


def test_two_running_jobs_run_in_id_order(data):
    for vid in (VID, "aaaaaaaaaaa"):
        queue(data, vid)
        with closing(db.connect(data)) as c:
            episodes.set_job_state(c, episodes.get_latest_job_with_steps(c, vid)["id"], "running")
    assert do_recover(data) == 2
    b = FakeBehavior()
    w = Worker(data, build_fakes(b))
    assert w.run_next() and states(data, VID)[0] == "done" and states(data, "aaaaaaaaaaa")[0] == "queued"
    assert w.run_next() and states(data, "aaaaaaaaaaa")[0] == "done"


def test_spend_and_vendor_id_kept(data):
    queue(data)
    with closing(db.connect(data)) as c:
        job = episodes.get_latest_job_with_steps(c, VID)
        episodes.set_job_state(c, job["id"], "running")
        episodes.set_step_state(c, job["id"], "transcribe", "running")
        episodes.set_vendor_job_id(c, job["id"], "transcribe", "vendor-1")
        c.execute("INSERT INTO spend_ledger (video_id, step, provider, amount_micro_usd, "
                  "created_at, provider_ref) VALUES (?, 'download', 'p', 5, 't', 'r')", (VID,))
    do_recover(data)
    with closing(db.connect(data)) as c:
        assert episodes.get_vendor_job_id(c, job["id"], "transcribe") == "vendor-1"
    assert ledger(data) == [("download", "p", 5, "r")]


# ---- kill and restart with fakes ---------------------------------------------------------

@pytest.mark.parametrize("step", ["transcribe", "summarize", "verify"])
def test_kill_mid_step_then_restart_completes(data, step):
    queue(data)
    adapters, b = killing(step, cost_micro=7)
    with pytest.raises(Killed):
        Worker(data, adapters).run_next()
    assert do_recover(data) == 1
    assert Worker(data, adapters).run_next()
    state, steps = states(data)
    assert state == "done" and set(steps.values()) == {"done"}
    # the kill happens before the adapter does any work, so every step did its work exactly once:
    # finished steps were not repeated and the killed one ran on resume
    for name in ("download", "transcribe", "summarize", "verify"):
        assert b.calls.count(name) == 1


def test_interrupted_llm_call_records_only_completed_calls(data):
    queue(data)
    adapters, _ = killing("summarize", cost_micro=7)
    # the kill happens before the fake charges, like a call that never returned
    with pytest.raises(Killed):
        Worker(data, adapters).run_next()
    steps_before = [r[0] for r in ledger(data)]
    assert "summarize" not in steps_before
    do_recover(data)
    Worker(data, adapters).run_next()
    assert [r[0] for r in ledger(data)].count("summarize") == 1


def test_resumed_transcribe_polls_saved_job_and_records_once(data, tmp_path):
    audio = tmp_path / "a.mp3"
    audio.write_bytes(b"x")
    client = tas.ScriptedClient([tas.DONE])
    resume = tas.Resume(saved="job-1")                  # saved before the kill
    meter = tas.Meter()
    tas.make(client).transcribe(audio, meter, resume)
    assert client.submits == [] and client.polled == ["job-1"]
    assert len(meter.rows) == 1 and meter.rows[0][2] == "job-1"


def test_ledger_unique_ref_blocks_duplicate_transcription_cost(data):
    queue(data)
    from app.core.meter import StepMeter
    with closing(db.connect(data)) as c:
        m = StepMeter(c, VID, "transcribe")
        m.record("assemblyai", 100, ref="job-1")
        m.record("assemblyai", 100, ref="job-1")
    assert len(ledger(data)) == 1


# ---- backoff helper ----------------------------------------------------------------------

def test_backoff_then_success_delays():
    sleeps, calls = [], []

    def fn():
        calls.append(1)
        if len(calls) < 3:
            raise ConnectionError("x")
        return "ok"
    assert call_with_backoff(fn, lambda e: isinstance(e, ConnectionError), 4, 5, 60,
                             sleeps.append) == "ok"
    assert sleeps == [5, 10] and len(calls) == 3


def test_backoff_gives_up_after_limit_with_same_error():
    sleeps = []
    err = ConnectionError("x")

    def fn():
        raise err
    with pytest.raises(ConnectionError) as e:
        call_with_backoff(fn, lambda e: True, 3, 5, 60, sleeps.append)
    assert e.value is err and sleeps == [5, 10, 20]


def test_backoff_delay_capped_and_zero_retries_and_permanent():
    sleeps = []

    def fn():
        raise ConnectionError("x")
    with pytest.raises(ConnectionError):
        call_with_backoff(fn, lambda e: True, 6, 5, 20, sleeps.append)
    assert sleeps == [5, 10, 20, 20, 20, 20]
    sleeps.clear()
    with pytest.raises(ConnectionError):
        call_with_backoff(fn, lambda e: True, 0, 5, 20, sleeps.append)
    assert sleeps == []
    calls = []

    def perm():
        calls.append(1)
        raise ValueError("no")
    with pytest.raises(ValueError):
        call_with_backoff(perm, lambda e: isinstance(e, ConnectionError), 4, 5, 20, sleeps.append)
    assert calls == [1] and sleeps == []


def test_default_retry_never_retries():
    assert Retry().retries == 0


# ---- adapters ----------------------------------------------------------------------------

class Flaky:
    """Raises the queued errors, then delegates to `inner.create` / `inner.submit`."""

    def __init__(self, inner, errors):
        self.inner, self.errors = inner, list(errors)
        self.attempts = 0

    def _maybe(self):
        self.attempts += 1
        if self.errors:
            raise self.errors.pop(0)

    def create(self, *a, **k):
        self._maybe()
        return self.inner.create(*a, **k)

    def submit(self, *a, **k):
        self._maybe()
        return self.inner.submit(*a, **k)

    def __getattr__(self, name):
        return getattr(self.inner, name)


def retry_with(sleeps, n=4):
    return Retry(n, 5, 60, sleeps.append)


def test_anthropic_transient_then_success_one_ledger_row():
    sleeps = []
    client = Flaky(ta.FakeClient(ta.good_reply()), [ant.Transient("HTTP 529")] * 2)
    meter = ta.Meter()
    ta.make(client, retry=retry_with(sleeps)).summarize(ta.TRANSCRIPT, meter, ta.DictCache())
    assert sleeps == [5, 10] and client.attempts == 3 and len(meter.rows) == 1


def test_anthropic_gives_up_with_todays_error():
    sleeps = []
    client = Flaky(ta.FakeClient(ta.good_reply()), [ant.Transient("HTTP 529")] * 9)
    meter = ta.Meter()
    with pytest.raises(StepError) as e:
        ta.make(client, retry=retry_with(sleeps)).summarize(ta.TRANSCRIPT, meter, ta.DictCache())
    assert e.value.retryable and "worth retrying" in e.value.message
    assert len(sleeps) == 4 and meter.rows == []


@pytest.mark.parametrize("err", [ant.AuthRejected(), ant.Refused("HTTP 400"), ant.TooLong()])
def test_anthropic_permanent_errors_not_retried(err):
    sleeps = []
    client = Flaky(ta.FakeClient(ta.good_reply()), [err])
    with pytest.raises(StepError):
        ta.make(client, retry=retry_with(sleeps)).summarize(ta.TRANSCRIPT, ta.Meter(), ta.DictCache())
    assert sleeps == [] and client.attempts == 1


def test_anthropic_default_no_retry():
    client = Flaky(ta.FakeClient(ta.good_reply()), [ant.Transient("HTTP 529")])
    with pytest.raises(StepError):
        ta.make(client).summarize(ta.TRANSCRIPT, ta.Meter(), ta.DictCache())
    assert client.attempts == 1


def test_openai_transient_then_success():
    sleeps = []
    inner = to.FakeClient(to.reply())
    client = Flaky(inner, [oa.Transient("HTTP 503")])
    v = oa.OpenAIVerifier("m", client_factory=lambda k: client, environ={oa.KEY_VAR: to.KEY},
                          retry=retry_with(sleeps))
    v.verify(to.TRANSCRIPT, to.PAGE, to.Meter(), None)
    assert sleeps == [5]


@pytest.mark.parametrize("err", [oa.AuthRejected(), oa.Refused("HTTP 400")])
def test_openai_permanent_errors_not_retried(err):
    sleeps = []
    client = Flaky(to.FakeClient(), [err])
    v = oa.OpenAIVerifier("m", client_factory=lambda k: client, environ={oa.KEY_VAR: to.KEY},
                          retry=retry_with(sleeps))
    with pytest.raises(StepError):
        v.verify(to.TRANSCRIPT, to.PAGE, to.Meter(), None)
    assert sleeps == [] and client.attempts == 1


def test_assemblyai_submit_retries_transient_only(tmp_path):
    audio = tmp_path / "a.mp3"
    audio.write_bytes(b"x")
    sleeps = []
    client = Flaky(tas.ScriptedClient([tas.DONE]), [aai.Transient("ConnectError")] * 2)
    t = tas.make(client, retry=retry_with(sleeps), compress_before_upload=False)
    t.transcribe(audio, tas.Meter(), tas.Resume())
    assert sleeps == [5, 10] and client.inner.submits == [audio]
    client = Flaky(tas.ScriptedClient([tas.DONE]), [aai.Refused("HTTP 400")])
    t = tas.make(client, retry=retry_with([]), compress_before_upload=False)
    with pytest.raises(StepError) as e:
        t.transcribe(audio, tas.Meter(), tas.Resume())
    assert not e.value.retryable and client.attempts == 1


# ---- config ------------------------------------------------------------------------------

def cfg(tmp_path, retry_block):
    base = (ROOT / "fake_config.toml").read_text()
    head, _, tail = base.partition("[retry]")
    rest = tail.split("[pricing]", 1)[1]
    p = tmp_path / "c.toml"
    p.write_text(head + retry_block + "\n[pricing]" + rest)
    return p


def test_config_defaults_and_values(tmp_path):
    c = load_config(cfg(tmp_path, ""))
    assert (c.max_retries, c.retry_base_delay_seconds, c.retry_max_delay_seconds) == (4, 5.0, 60.0)
    c = load_config(cfg(tmp_path, "[retry]\nmax_retries = 0\nbase_delay_seconds = 1\nmax_delay_seconds = 2\n"))
    assert (c.max_retries, c.retry_base_delay_seconds, c.retry_max_delay_seconds) == (0, 1.0, 2.0)
    r = Retry.from_config(c)
    assert (r.retries, r.base_delay, r.max_delay) == (0, 1.0, 2.0)


@pytest.mark.parametrize("block", [
    "[retry]\nmax_retries = -1\n", "[retry]\nmax_retries = 11\n", "[retry]\nmax_retries = 1.5\n",
    "[retry]\nmax_retries = true\n", "[retry]\nbase_delay_seconds = 0\n",
    "[retry]\nbase_delay_seconds = 10\nmax_delay_seconds = 5\n",
])
def test_config_rejects_bad_retry(tmp_path, block):
    with pytest.raises(ConfigError):
        load_config(cfg(tmp_path, block))


# ---- review patches ----------------------------------------------------------------------

def test_from_config_passes_retry_to_every_real_adapter(tmp_path):
    c = load_config(cfg(tmp_path, "[retry]\nmax_retries = 7\nbase_delay_seconds = 2\n"
                                   "max_delay_seconds = 30\n"))
    expected = Retry(7, 2.0, 30.0)
    assert ant.AnthropicSummarizer.from_config(c).retry == expected
    assert oa.OpenAIVerifier.from_config(c).retry == expected
    assert aai.AssemblyAITranscriber.from_config(c).retry == expected


def test_default_sleep_stops_at_once_when_shutdown_requested():
    import time
    from app.core import retry
    retry.request_stop()
    try:
        started = time.monotonic()
        with pytest.raises(retry.Stopping):
            retry.stop_aware_sleep(30)
        assert time.monotonic() - started < 1
        calls = []

        def fn():
            calls.append(1)
            raise ConnectionError("x")
        with pytest.raises(retry.Stopping):
            Retry(4, 5, 60).call(fn, lambda e: True)
        assert calls == [1]            # no second attempt after the stop
    finally:
        retry.clear_stop()


def test_stop_during_backoff_wait_leaves_job_running_and_returns_quickly(data):
    import threading
    import time
    queue(data)
    waiting = threading.Event()

    class Slow:
        def create(self, *a, **k):
            waiting.set()
            raise ant.Transient("HTTP 529")

    summarizer = ant.AnthropicSummarizer(
        "m", client_factory=lambda key: Slow(), environ={ant.KEY_VAR: "k"},
        retry=Retry(4, 60, 60))
    adapters = build_fakes()
    from dataclasses import replace
    adapters = replace(adapters, summarizer=summarizer)
    w = Worker(data, adapters, poll_interval=0.05)
    w.start()
    assert waiting.wait(10)
    time.sleep(0.2)                    # now inside the 60 s backoff wait
    started = time.monotonic()
    w.stop()
    assert time.monotonic() - started < 3
    assert w._thread is None
    state, steps = states(data)
    assert state == "running" and steps["summarize"] == "running"
    assert do_recover(data) == 1
