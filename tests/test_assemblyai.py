import os
import shutil
import sqlite3
import subprocess
from contextlib import closing
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.adapters import assemblyai as aai
from app.adapters.assemblyai import (
    AssemblyAITranscriber, AuthRejected, JobGone, JobStatus, Refused, Transient, cost_micro_usd,
)
from app.adapters.fakes import build_fakes
from app.config import load_config
from app.core import submit
from app.core.meter import StepMeter
from app.core.pipeline import StepResume
from app.ports import Segment, StepError, Transcript
from app.store import artifacts, db, episodes, migrations
from app.web.app import create_app
from app.worker import Worker, build_adapters

VID = "dQw4w9WgXcQ"
KEY = "sk-secret-key-123"
ROOT = Path(__file__).resolve().parent
UTTERANCES = [Segment(0.0, 4.0, "Hello there.", "A"), Segment(4.0, 9.5, "Hi <b>you</b>.", "B")]
SENTENCES = [Segment(0.0, 2.0, "Hello there."), Segment(2.0, 9.5, "Hi you.")]


class ScriptedClient:
    """Stands in for SdkClient. `script` items are JobStatus or an exception to raise."""

    def __init__(self, script=(), submit_error=None, job_id="job-1"):
        self.script = list(script)
        self.submit_error = submit_error
        self.job_id = job_id
        self.submits = []
        self.polled = []

    def submit(self, audio_path):
        if self.submit_error:
            raise self.submit_error
        self.submits.append(audio_path)
        return self.job_id

    def status(self, job_id):
        self.polled.append(job_id)
        item = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        if isinstance(item, Exception):
            raise item
        return item

    def utterances(self, job_id):
        return list(UTTERANCES)

    def sentences(self, job_id):
        return list(SENTENCES)


class Meter:
    def __init__(self):
        self.rows = []

    def record(self, provider, amount, ref=None):
        self.rows.append((provider, amount, ref))


class Resume:
    def __init__(self, saved=None):
        self.saved, self.events = saved, []

    def load(self):
        return self.saved

    def save(self, i):
        self.events.append(("save", i))
        self.saved = i

    def clear(self):
        self.events.append(("clear",))
        self.saved = None


DONE = JobStatus("completed", audio_duration=3600)


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, s):
        self.now += s


def make(client, **kw):
    clock = Clock()
    t = AssemblyAITranscriber(
        ["universal-3-5-pro"], client_factory=lambda key: client, sleep=clock.sleep,
        clock=clock, environ=kw.pop("environ", {aai.KEY_VAR: KEY}), **kw)
    return t


def run(client, resume=None, meter=None, **kw):
    resume = resume or Resume()
    meter = meter or Meter()
    return make(client, **kw).transcribe(Path("a.m4a"), meter, resume), meter, resume


def test_happy_path_saves_id_before_first_poll_and_records_once():
    order = []
    client = ScriptedClient([JobStatus("queued"), JobStatus("processing"), DONE])
    resume = Resume()
    orig_status, orig_save = client.status, resume.save
    client.status = lambda i: (order.append("poll"), orig_status(i))[1]
    resume.save = lambda i: (order.append("save"), orig_save(i))[1]
    t, meter, _ = run(client, resume)
    assert order[0] == "save" and order.count("poll") == 3
    assert meter.rows == [("assemblyai", 230000, "job-1")]
    assert t.segments == tuple(UTTERANCES)


@pytest.mark.parametrize("seconds,micro", [(3600, 230000), (21600, 1380000), (1, 64)])
def test_cost_math(seconds, micro):
    assert cost_micro_usd(seconds, 0.23) == micro


def test_cost_falls_back_to_last_segment_end():
    _, meter, _ = run(ScriptedClient([JobStatus("completed")]))
    assert meter.rows[0][1] == cost_micro_usd(9.5, 0.23)


def test_resume_polls_saved_id_without_submitting():
    client = ScriptedClient([DONE])
    run(client, Resume("old-id"))
    assert client.submits == [] and client.polled == ["old-id"]


def test_vendor_error_is_permanent_and_clears_id():
    client = ScriptedClient([JobStatus("error", error="no spoken audio")])
    resume, meter = Resume(), Meter()
    with pytest.raises(StepError) as e:
        run(client, resume, meter)
    assert not e.value.retryable and "no spoken audio" in e.value.message
    assert resume.saved is None and ("clear",) in resume.events and meter.rows == []


def test_missing_key_names_variable_and_submits_nothing():
    client = ScriptedClient([DONE])
    with pytest.raises(StepError) as e:
        make(client, environ={}).transcribe(Path("a"), Meter(), Resume())
    assert e.value.retryable and aai.KEY_VAR in e.value.message and client.submits == []


def test_rejected_key_on_submit_and_poll():
    for client in (ScriptedClient(submit_error=AuthRejected()), ScriptedClient([AuthRejected()])):
        with pytest.raises(StepError) as e:
            run(client)
        assert e.value.retryable and "rejected" in e.value.message and KEY not in e.value.message


def test_transient_poll_errors_recover():
    t, meter, _ = run(ScriptedClient([Transient("x"), Transient("x"), DONE]))
    assert len(meter.rows) == 1 and t.segments


def test_persistent_poll_errors_fail_retryable_keeping_id():
    client = ScriptedClient([Transient("x")])
    resume = Resume()
    with pytest.raises(StepError) as e:
        run(client, resume)
    assert e.value.retryable and len(client.polled) == 6
    assert resume.saved == "job-1" and ("clear",) not in resume.events


def test_five_consecutive_errors_are_still_tolerated():
    run(ScriptedClient([Transient("x")] * 5 + [DONE]))


def test_wait_timeout_keeps_id():
    client = ScriptedClient([JobStatus("processing")])
    resume = Resume()
    with pytest.raises(StepError) as e:
        run(client, resume, max_wait_minutes=1, poll_interval_seconds=5)
    assert e.value.retryable and resume.saved == "job-1"


def test_submit_failures():
    resume = Resume()
    with pytest.raises(StepError) as e:
        run(ScriptedClient(submit_error=Transient("ConnectError")), resume)
    assert e.value.retryable and resume.events == []
    with pytest.raises(StepError) as e:
        run(ScriptedClient(submit_error=Refused("HTTP 400")))
    assert not e.value.retryable


def test_job_gone_clears_id():
    resume = Resume("stale")
    with pytest.raises(StepError) as e:
        run(ScriptedClient([JobGone()]), resume)
    assert e.value.retryable and resume.saved is None


def test_large_file_is_one_submit():
    client = ScriptedClient([JobStatus("completed", audio_duration=21600)])
    _, meter, _ = run(client)
    assert len(client.submits) == 1 and meter.rows[0][1] == 1380000


def test_speaker_labels_off_gives_sentences_without_speaker():
    t, _, _ = run(ScriptedClient([DONE]), speaker_labels=False)
    assert t.segments == tuple(SENTENCES) and all(s.speaker is None for s in t.segments)


def test_segment_from_dict_tolerates_missing_speaker():
    t = Transcript.from_dict({"segments": [{"start": 0, "end": 1, "text": "x"}]})
    assert t.segments[0].speaker is None
    assert Transcript.from_dict(t.to_dict()) == t


# --- persistence and pipeline wiring ---

@pytest.fixture
def data(tmp_path):
    db.bootstrap(tmp_path)
    return tmp_path


def test_ledger_ref_is_idempotent(data):
    with closing(db.connect(data)) as c:
        submit.submit(c, f"https://www.youtube.com/watch?v={VID}")
        m = StepMeter(c, VID, "transcribe")
        for _ in range(2):
            m.record("assemblyai", 5, ref="job-1")
        m.record("assemblyai", 5, ref="job-2")
        m.record("assemblyai", 5)
        m.record("assemblyai", 5)
        assert c.execute("select count(*) from spend_ledger").fetchone()[0] == 4


def test_resume_handle_survives_step_state_changes(data):
    with closing(db.connect(data)) as c:
        submit.submit(c, f"https://www.youtube.com/watch?v={VID}")
        job = episodes.next_queued_job(c)
        r = StepResume(c, job["id"], "transcribe")
        assert r.load() is None
        r.save("job-9")
        episodes.set_step_state(c, job["id"], "transcribe", "failed", "x", True)
        episodes.set_step_state(c, job["id"], "transcribe", "running")
        assert StepResume(c, job["id"], "transcribe").load() == "job-9"
        r.clear()
        assert r.load() is None


def test_pipeline_end_to_end_with_scripted_client_and_failed_retry(data):
    client = ScriptedClient([JobStatus("error", error="no spoken audio")])
    t = make(client)
    adapters = build_fakes()
    from dataclasses import replace
    w = Worker(data, replace(adapters, transcriber=t), secrets=lambda: [KEY])
    with closing(db.connect(data)) as c:
        submit.submit(c, f"https://www.youtube.com/watch?v={VID}")
    w.run_next()
    with closing(db.connect(data)) as c:
        steps = {s["name"]: s for s in episodes.get_latest_job_with_steps(c, VID)["steps"]}
    assert steps["transcribe"]["state"] == "failed" and steps["transcribe"]["retryable"] == 0
    assert "no spoken audio" in steps["transcribe"]["message"]


def test_upgrade_from_populated_v3(tmp_path):
    conn = sqlite3.connect(tmp_path / db.DB_NAME, isolation_level=None)
    for stmts in migrations.MIGRATIONS[:3]:
        for s in stmts:
            conn.execute(s)
    conn.execute("PRAGMA user_version = 3")
    conn.execute("insert into episodes (video_id,url,created_at) values ('x','u','n')")
    conn.execute("insert into spend_ledger (video_id,step,provider,amount_micro_usd,created_at)"
                 " values ('x','download','fake',7,'t')")
    conn.close()
    db.bootstrap(tmp_path)
    with closing(db.connect(tmp_path)) as c:
        assert c.execute("PRAGMA user_version").fetchone()[0] == 9
        assert c.execute("select amount_micro_usd, provider_ref from spend_ledger"
                         ).fetchall() == [(7, None)]


# --- web ---

def test_transcript_page(data):
    client = TestClient(create_app([], data, 5.0))
    with closing(db.connect(data)) as c:
        submit.submit(c, f"https://www.youtube.com/watch?v={VID}")
    assert client.get(f"/episodes/{VID}/transcript").status_code == 404
    assert "/transcript" not in client.get(f"/episodes/{VID}/status").text
    artifacts.write_json(
        artifacts.artifact_path(data, VID, artifacts.TRANSCRIPT),
        Transcript((Segment(0, 4, "Hello."), Segment(3725, 3730, "<script>x</script>", "A"))
                   ).to_dict())
    body = client.get(f"/episodes/{VID}/transcript").text
    assert "[00:00] Hello." in body and "[1:02:05] A: &lt;script&gt;x&lt;/script&gt;" in body
    with closing(db.connect(data)) as c:
        job = episodes.next_queued_job(c)
        episodes.set_step_state(c, job["id"], "transcribe", "done")
    assert f"/episodes/{VID}/transcript" in client.get(f"/episodes/{VID}/status").text


def test_transcript_page_unknown_episode_is_404(data):
    assert TestClient(create_app([], data, 5.0)).get("/episodes/zzzzzzzzzzz/transcript"
                                                     ).status_code == 404


# --- config ---

def test_build_adapters_accepts_assemblyai(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text((ROOT / "fake_config.toml").read_text().replace(
        'transcriber = "fake"', 'transcriber = "assemblyai"'))
    a = build_adapters(load_config(cfg))
    assert isinstance(a.transcriber, AssemblyAITranscriber)
    assert a.transcriber.usd_per_hour == 0.23 and a.transcriber.max_wait_seconds == 180 * 60
    assert type(build_adapters(load_config(ROOT / "fake_config.toml")).transcriber
                ).__name__ == "FakeTranscriber"


@pytest.mark.parametrize("old,new", [
    ("poll_interval_seconds = 5", "poll_interval_seconds = 0"),
    ("max_wait_minutes = 180", 'max_wait_minutes = "x"'),
    ("speaker_labels = true", "speaker_labels = 1"),
    ("transcription_usd_per_hour = 0.23", "transcription_usd_per_hour = -1"),
])
def test_bad_new_config_keys_rejected(tmp_path, old, new):
    from app.config import ConfigError
    cfg = tmp_path / "config.toml"
    text = (ROOT / "fake_config.toml").read_text()
    assert old in text
    cfg.write_text(text.replace(old, new))
    with pytest.raises(ConfigError):
        load_config(cfg)


def test_real_config_uses_the_real_transcriber():
    assert load_config().providers["transcriber"] == "assemblyai"


# --- network smoke test ---

@pytest.mark.network
@pytest.mark.skipif(os.environ.get("RUN_NETWORK_TESTS") != "1"
                    or not os.environ.get("ASSEMBLYAI_API_KEY"),
                    reason="set RUN_NETWORK_TESTS=1 and ASSEMBLYAI_API_KEY")
def test_real_transcription(tmp_path):
    if not shutil.which("say") or not shutil.which("ffmpeg"):
        pytest.skip("needs macOS say and ffmpeg")
    aiff, m4a = tmp_path / "s.aiff", tmp_path / "s.m4a"
    subprocess.run(["say", "-o", str(aiff), "Hello, this is a short test of the podcast "
                    "transcription pipeline."], check=True)
    subprocess.run(["ffmpeg", "-y", "-i", str(aiff), str(m4a)], check=True, capture_output=True)
    t = AssemblyAITranscriber.from_config(load_config())
    meter, resume = Meter(), Resume()
    result = t.transcribe(m4a, meter, resume)
    assert "podcast" in result.text.lower()
    assert 0 < meter.rows[0][1] < 10_000 and meter.rows[0][2] == resume.saved


# --- review patches: mapping layer, empty results, unknown states, real success path ---

def _bare_sdk(aai_stub=None, http=None):
    c = aai.SdkClient.__new__(aai.SdkClient)
    c._aai = aai_stub
    c._client = type("C", (), {"http_client": http})()
    return c


class _Resp:
    def __init__(self, code, body=None):
        self.status_code, self._body = code, body

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


class _Http:
    def __init__(self, resp):
        self.resp = resp

    def get(self, path):
        return self.resp


@pytest.mark.parametrize("code,kind", [
    (401, AuthRejected), (403, AuthRejected), (404, JobGone), (400, Refused), (422, Refused),
    (408, Transient), (425, Transient), (429, Transient), (500, Transient), (503, Transient),
])
def test_sdk_error_mapping_by_status_code(code, kind):
    import httpx
    exc = httpx.HTTPStatusError("x", request=httpx.Request("GET", "http://x"),
                                response=httpx.Response(code))
    assert isinstance(aai.SdkClient._map(exc), kind)
    attr = type("E", (Exception,), {"status_code": code})()
    assert isinstance(aai.SdkClient._map(attr), kind)


def test_sdk_non_http_error_is_transient_and_hides_details():
    mapped = aai.SdkClient._map(RuntimeError(f"boom {KEY}"))
    assert isinstance(mapped, Transient) and KEY not in str(mapped)


def test_sdk_status_parses_body_and_maps_polling_errors():
    ok = _bare_sdk(http=_Http(_Resp(200, {"status": "completed", "audio_duration": 61.5})))
    assert ok.status("j") == JobStatus("completed", None, 61.5)
    err = _bare_sdk(http=_Http(_Resp(200, {"status": "error", "error": "bad audio"})))
    assert err.status("j") == JobStatus("error", "bad audio", None)
    for code, kind in ((401, AuthRejected), (404, JobGone), (400, Transient), (500, Transient)):
        with pytest.raises(kind):
            _bare_sdk(http=_Http(_Resp(code))).status("j")
    with pytest.raises(Transient):
        _bare_sdk(http=_Http(_Resp(200, ["not", "an", "object"]))).status("j")
    with pytest.raises(Transient):
        _bare_sdk(http=_Http(_Resp(200, ValueError("bad json")))).status("j")


def test_sdk_segments_convert_milliseconds_and_empty_speaker():
    assert aai._seg(1500, 4000, "hi", "A") == Segment(1.5, 4.0, "hi", "A")
    assert aai._seg(0, 10, "hi", "").speaker is None

    class T:
        utterances = [type("U", (), {"start": 2000, "end": 3500, "text": "yo", "speaker": "B"})()]

        @staticmethod
        def get_sentences():
            return [type("S", (), {"start": 100, "end": 900, "text": "s"})()]

    c = _bare_sdk()
    c._finished = lambda job_id: T
    assert c.utterances("j") == [Segment(2.0, 3.5, "yo", "B")]
    assert c.sentences("j") == [Segment(0.1, 0.9, "s")]


def test_sdk_submit_rejects_vendor_error_status():
    class Status:
        error = "error"

    class Tr:
        status, id, error = "error", "x", "unsupported audio"

    class Stub:
        TranscriptStatus = Status

        class Transcriber:
            def __init__(self, client=None):
                pass

            def submit(self, path, config):
                return Tr()

    c = _bare_sdk(Stub)
    c._config = None
    with pytest.raises(Refused, match="unsupported audio"):
        c.submit(Path("a.m4a"))


def test_empty_speaker_turns_fall_back_to_sentences():
    class NoTurns(ScriptedClient):
        def utterances(self, job_id):
            return []

    t, meter, _ = run(NoTurns([DONE]))
    assert t.segments == tuple(SENTENCES) and len(meter.rows) == 1


def test_no_speech_at_all_is_permanent_but_still_billed():
    class Nothing(ScriptedClient):
        def utterances(self, job_id):
            return []

        def sentences(self, job_id):
            return []

    meter, resume = Meter(), Resume()
    with pytest.raises(StepError) as e:
        make(Nothing([DONE])).transcribe(Path("a.m4a"), meter, resume)
    assert e.value.retryable is False and meter.rows == [("assemblyai", 230000, "job-1")]


def test_job_gone_while_fetching_clears_id():
    class Gone(ScriptedClient):
        def utterances(self, job_id):
            raise JobGone()

    resume = Resume()
    with pytest.raises(StepError) as e:
        make(Gone([DONE])).transcribe(Path("a.m4a"), Meter(), resume)
    assert e.value.retryable is True and ("clear",) in resume.events


def test_unknown_state_fails_retryable_instead_of_polling_for_hours():
    client = ScriptedClient([JobStatus("mystery")])
    with pytest.raises(StepError) as e:
        run(client)
    assert e.value.retryable is True and len(client.polled) == 6


def test_ledger_ref_conflict_only_skips_duplicates_not_other_violations(data):
    from app.store import spend
    with closing(db.connect(data)) as c:
        submit.submit(c, f"https://www.youtube.com/watch?v={VID}")
        spend.append(c, VID, "transcribe", "assemblyai", 5, ref="j1")
        spend.append(c, VID, "transcribe", "assemblyai", 5, ref="j1")
        assert c.execute("select count(*) from spend_ledger").fetchone()[0] == 1
        with pytest.raises(sqlite3.IntegrityError):
            spend.append(c, VID, "transcribe", None, 5, ref="j2")  # NOT NULL still enforced
        with pytest.raises(sqlite3.IntegrityError):
            spend.append(c, "nonexistent", "transcribe", "assemblyai", 5, ref="j3")  # FK


def test_set_vendor_job_id_for_a_missing_step_raises(data):
    with closing(db.connect(data)) as c:
        with pytest.raises(LookupError):
            episodes.set_vendor_job_id(c, 999, "transcribe", "x")


def test_worker_success_path_persists_id_ledger_row_speaker_and_does_not_repay(data):
    from dataclasses import replace
    client = ScriptedClient([JobStatus("processing"), DONE])
    w = Worker(data, replace(build_fakes(), transcriber=make(client)), secrets=lambda: [KEY])
    with closing(db.connect(data)) as c:
        submit.submit(c, f"https://www.youtube.com/watch?v={VID}")
    w.run_next()
    with closing(db.connect(data)) as c:
        job = episodes.get_latest_job_with_steps(c, VID)
        stored = c.execute("select vendor_job_id from steps where job_id=? and name='transcribe'",
                           (job["id"],)).fetchone()[0]
        ledger = c.execute("select step, provider, amount_micro_usd, provider_ref "
                           "from spend_ledger where step='transcribe'").fetchall()
        assert job["state"] == "done" and stored == "job-1"
        assert ledger == [("transcribe", "assemblyai", 230000, "job-1")]
        episodes.set_job_state(c, job["id"], "queued")
        c.execute("update steps set state='pending' where job_id=? and name='transcribe'",
                  (job["id"],))
    saved = artifacts.read_json(artifacts.artifact_path(data, VID, artifacts.TRANSCRIPT))
    assert saved["segments"][0]["speaker"] == "A"
    artifacts.artifact_path(data, VID, artifacts.TRANSCRIPT).unlink()
    client.script = [DONE]
    w.run_next()  # same job ID resumed: no second submit, no second ledger row
    assert len(client.submits) == 1
    with closing(db.connect(data)) as c:
        assert c.execute("select count(*) from spend_ledger where step='transcribe'"
                         ).fetchone()[0] == 1


def test_transcript_page_with_corrupt_artifact_is_404_not_500(data):
    with closing(db.connect(data)) as c:
        submit.submit(c, f"https://www.youtube.com/watch?v={VID}")
    artifacts.write_json(artifacts.artifact_path(data, VID, artifacts.TRANSCRIPT),
                         {"segments": [{"start": "soon", "end": 1, "text": "x"}]})
    client = TestClient(create_app([], data))
    assert client.get(f"/episodes/{VID}/transcript").status_code == 404


# --- found in the first supervised real run (Story 1.11) ---

def test_audio_is_compressed_before_upload_and_the_copy_is_removed(tmp_path):
    audio = tmp_path / "audio.m4a"
    audio.write_bytes(b"x" * 1000)
    seen = {}

    def compressor(src, dest, kbps):
        seen["args"] = (src, dest.name, kbps)
        dest.write_bytes(b"y" * 100)

    class Client(ScriptedClient):
        def submit(self, audio_path):
            seen["uploaded"] = (audio_path.name, audio_path.stat().st_size)
            return super().submit(audio_path)

    t = AssemblyAITranscriber(["m"], client_factory=lambda k: Client([DONE]), compressor=compressor,
                              upload_bitrate_kbps=24, environ={aai.KEY_VAR: KEY},
                              sleep=lambda s: None)
    t.transcribe(audio, Meter(), Resume())
    assert seen["args"] == (audio, ".upload-audio.m4a", 24)
    assert seen["uploaded"] == (".upload-audio.m4a", 100)       # the small copy was sent
    assert sorted(p.name for p in tmp_path.iterdir()) == ["audio.m4a"]  # copy removed
    assert audio.read_bytes() == b"x" * 1000                            # original untouched


@pytest.mark.parametrize("failure", [FileNotFoundError("ffmpeg"), RuntimeError("bad"),
                                     "empty"])
def test_compression_failure_falls_back_to_the_original(tmp_path, failure):
    audio = tmp_path / "audio.m4a"
    audio.write_bytes(b"x" * 10)

    def compressor(src, dest, kbps):
        if failure == "empty":
            dest.write_bytes(b"")
        else:
            raise failure

    sent = []

    class Client(ScriptedClient):
        def submit(self, audio_path):
            sent.append(audio_path.name)
            return super().submit(audio_path)

    t = AssemblyAITranscriber(["m"], client_factory=lambda k: Client([DONE]), compressor=compressor,
                              environ={aai.KEY_VAR: KEY}, sleep=lambda s: None)
    t.transcribe(audio, Meter(), Resume())
    assert sent == ["audio.m4a"] and sorted(p.name for p in tmp_path.iterdir()) == ["audio.m4a"]


def test_no_compression_when_resuming_or_when_switched_off(tmp_path):
    audio = tmp_path / "audio.m4a"
    audio.write_bytes(b"x")
    calls = []
    comp = lambda src, dest, kbps: calls.append(1)
    t = AssemblyAITranscriber(["m"], client_factory=lambda k: ScriptedClient([DONE]),
                              compressor=comp, environ={aai.KEY_VAR: KEY}, sleep=lambda s: None)
    t.transcribe(audio, Meter(), Resume("saved-id"))        # resuming: nothing to upload
    off = AssemblyAITranscriber(["m"], client_factory=lambda k: ScriptedClient([DONE]),
                                compress_before_upload=False, compressor=comp,
                                environ={aai.KEY_VAR: KEY}, sleep=lambda s: None)
    off.transcribe(audio, Meter(), Resume())
    assert calls == []


def test_upload_failure_shows_the_vendor_reason_without_urls_or_the_key():
    class TranscriptError(Exception):
        pass
    TranscriptError.__module__ = "assemblyai.types"
    err = TranscriptError(
        "failed to transcribe url https://cdn.assemblyai.com/upload/abc-123: "
        "audio file could not be processed")
    mapped = aai.SdkClient._map(err)
    assert isinstance(mapped, Transient)
    assert "audio file could not be processed" in str(mapped)
    assert "https://" not in str(mapped) and KEY not in str(mapped)
    with pytest.raises(StepError) as e:
        run(ScriptedClient(submit_error=mapped))
    assert "audio file could not be processed" in e.value.message and e.value.retryable


def test_unexpected_exception_text_is_not_shown():
    assert "secret" not in str(aai.SdkClient._map(RuntimeError("secret " + KEY)))


def test_ffmpeg_compress_really_shrinks_speech_audio(tmp_path):
    import shutil
    import subprocess
    if not shutil.which("ffmpeg") or not shutil.which("say"):
        pytest.skip("needs ffmpeg and macOS say")
    src = tmp_path / "in.aiff"
    subprocess.run(["say", "-o", str(src), "Hello there. This is a short test of compression."],
                   check=True)
    big = tmp_path / "big.m4a"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-b:a", "128k",
                    str(big)], check=True)
    small = tmp_path / "small.m4a"
    aai.ffmpeg_compress(big, small, 32)
    assert 0 < small.stat().st_size < big.stat().st_size


@pytest.mark.parametrize("old,new,key", [
    ("compress_before_upload = true", "compress_before_upload = 1", "compress_before_upload"),
    ("upload_bitrate_kbps = 32", "upload_bitrate_kbps = 15", "upload_bitrate_kbps"),
    ("upload_bitrate_kbps = 32", "upload_bitrate_kbps = true", "upload_bitrate_kbps"),
    ("upload_bitrate_kbps = 32", "upload_bitrate_kbps = 129", "upload_bitrate_kbps"),
    ("upload_bitrate_kbps = 32", 'upload_bitrate_kbps = "x"', "upload_bitrate_kbps"),
])
def test_bad_upload_config_rejected(tmp_path, old, new, key):
    f = tmp_path / "c.toml"
    f.write_text((ROOT / "fake_config.toml").read_text().replace(old, new))
    with pytest.raises(Exception, match=key):
        load_config(f)


def test_a_compressed_copy_that_is_not_smaller_is_not_used(tmp_path):
    audio = tmp_path / "audio.m4a"
    audio.write_bytes(b"x" * 100)
    sent = []

    class Client(ScriptedClient):
        def submit(self, audio_path):
            sent.append(audio_path.name)
            return super().submit(audio_path)

    def compressor(src, dest, kbps):
        dest.write_bytes(b"y" * 500)          # bigger than the source

    t = AssemblyAITranscriber(["m"], client_factory=lambda k: Client([DONE]), compressor=compressor,
                              environ={aai.KEY_VAR: KEY}, sleep=lambda s: None)
    t.transcribe(audio, Meter(), Resume())
    assert sent == ["audio.m4a"] and sorted(p.name for p in tmp_path.iterdir()) == ["audio.m4a"]


def test_compression_failure_is_logged_not_silent(tmp_path, caplog):
    audio = tmp_path / "audio.m4a"
    audio.write_bytes(b"x")

    def compressor(src, dest, kbps):
        raise RuntimeError("ffmpeg exited with 1: Unknown encoder")

    t = AssemblyAITranscriber(["m"], client_factory=lambda k: ScriptedClient([DONE]),
                              compressor=compressor, environ={aai.KEY_VAR: KEY},
                              sleep=lambda s: None)
    with caplog.at_level("WARNING", logger="app.adapters.assemblyai"):
        t.transcribe(audio, Meter(), Resume())
    assert "uploading the original" in caplog.text and "Unknown encoder" in caplog.text
    assert KEY not in caplog.text


def test_ffmpeg_compress_command_and_failure_modes(tmp_path, monkeypatch):
    import subprocess
    calls = {}

    def fake_run(argv, **kw):
        calls["argv"], calls["kw"] = argv, kw
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    monkeypatch.setattr(aai.shutil, "which", lambda name: "/usr/bin/ffmpeg")
    monkeypatch.setattr(aai.subprocess, "run", fake_run)
    aai.ffmpeg_compress(tmp_path / "in.m4a", tmp_path / "out.m4a", 24)
    a = calls["argv"]
    assert a[0] == "/usr/bin/ffmpeg" and a[a.index("-ac") + 1] == "1"
    assert a[a.index("-ar") + 1] == "16000" and a[a.index("-b:a") + 1] == "24k"
    assert "-vn" in a and a[-1].endswith("out.m4a") and calls["kw"]["check"] is True
    assert calls["kw"]["timeout"] <= 600

    def failing(argv, **kw):
        raise subprocess.CalledProcessError(1, argv, stderr=b"Unknown encoder 'aac'")

    monkeypatch.setattr(aai.subprocess, "run", failing)
    with pytest.raises(RuntimeError, match="exited with 1.*Unknown encoder"):
        aai.ffmpeg_compress(tmp_path / "in.m4a", tmp_path / "out.m4a", 24)
    monkeypatch.setattr(aai.shutil, "which", lambda name: None)
    with pytest.raises(FileNotFoundError):
        aai.ffmpeg_compress(tmp_path / "in.m4a", tmp_path / "out.m4a", 24)


def test_describe_redacts_the_configured_key(monkeypatch):
    class TranscriptError(Exception):
        pass
    TranscriptError.__module__ = "assemblyai.types"
    monkeypatch.setenv(aai.KEY_VAR, KEY)
    assert KEY not in aai._describe(TranscriptError(f"bad header {KEY}"))


def test_generic_submit_failure_message_uses_describe(tmp_path):
    with pytest.raises(StepError) as e:
        run(ScriptedClient(submit_error=RuntimeError("boom " + KEY)))
    assert "RuntimeError" in e.value.message and KEY not in e.value.message


def test_from_config_passes_the_upload_settings(tmp_path):
    f = tmp_path / "c.toml"
    f.write_text((ROOT / "fake_config.toml").read_text()
                 .replace("compress_before_upload = true", "compress_before_upload = false")
                 .replace("upload_bitrate_kbps = 32", "upload_bitrate_kbps = 24"))
    t = AssemblyAITranscriber.from_config(load_config(f))
    assert t.compress_before_upload is False and t.upload_bitrate_kbps == 24


def test_shipped_config_builds_real_adapters():
    adapters = build_adapters(load_config())
    assert type(adapters.transcriber).__name__ == "AssemblyAITranscriber"
    assert type(adapters.summarizer).__name__ == "AnthropicSummarizer"
    assert type(adapters.downloader).__name__ == "YtDlpDownloader"
    assert type(adapters.verifier).__name__ == "OpenAIVerifier"
