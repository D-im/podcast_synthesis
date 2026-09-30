import os
import socket
from contextlib import closing
from pathlib import Path

import pytest

from app.adapters.ytdlp import WORK_DIR, YtDlpDownloader, classify_error
from app.core import submit
from app.adapters.fakes import build_fakes
from app.ports import Adapters, StepError
from app.store import artifacts, db, episodes
from app.worker import Worker

VID = "dQw4w9WgXcQ"
URL = f"https://www.youtube.com/watch?v={VID}"


class Meter:
    def __init__(self):
        self.calls = []

    def record(self, provider, amount):
        self.calls.append((provider, amount))


class FakeYDL:
    """Stands in for yt_dlp.YoutubeDL. `behavior(opts, url)` returns info or raises."""

    def __init__(self, behavior, seen):
        self.behavior, self.seen = behavior, seen

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def extract_info(self, url, download=True):
        return self.behavior(self.seen["opts"], url)


def make(behavior):
    seen = {}

    def factory(opts):
        seen["opts"] = opts
        return FakeYDL(behavior, seen)

    return YtDlpDownloader("node", factory), seen


def good(title="T", duration=61.4, write=True):
    def b(opts, url):
        if write:
            out = Path(opts["outtmpl"]).parent
            (out / "audio.m4a").write_bytes(b"m4a")
            (out / "audio.webm.part").write_bytes(b"junk")
        return {"title": title, "duration": duration}
    return b


@pytest.fixture
def dest(tmp_path):
    d = tmp_path / "ep"
    d.mkdir()
    return d / "audio.m4a.tmp"


def listing(dest):
    return sorted(p.name for p in dest.parent.iterdir())


def test_success(dest):
    dl, seen = make(good("x" * 400, 61.6))
    m = Meter()
    r = dl.download(VID, URL, dest, m)
    assert r.title == "x" * 300 and r.duration_seconds == 62
    assert dest.read_bytes() == b"m4a"
    assert listing(dest) == [dest.name]
    assert m.calls == []
    o = seen["opts"]
    assert o["noplaylist"] and o["quiet"] and o["noprogress"]
    assert o["js_runtimes"] == {"node": {}}
    assert o["postprocessors"][0]["preferredcodec"] == "m4a"
    assert o["socket_timeout"] and o["retries"] is not None


def test_ytdlp_accepts_runtime_option_shape():
    yt_dlp = pytest.importorskip("yt_dlp")
    with yt_dlp.YoutubeDL({"js_runtimes": {"node": {}}, "quiet": True}):
        pass


def fail_with(exc):
    def b(opts, url):
        (Path(opts["outtmpl"]).parent / "audio.webm.part").write_bytes(b"p")
        raise exc
    return b


def dl_error(msg):
    from yt_dlp.utils import DownloadError
    return DownloadError(msg)


@pytest.mark.parametrize("msg,retryable,needle", [
    ("ERROR: [youtube] x: Private video. Sign in if you've been granted access", False, "private"),
    ("ERROR: [youtube] x: Video unavailable", False, "unavailable"),
    ("ERROR: [youtube] x: This video has been removed by the uploader", False, "removed"),
    ("ERROR: [youtube] x: Join this channel to get access to members-only content", False,
     "members-only"),
    ("ERROR: [youtube] x: Sign in to confirm your age", False, "age"),
    ("ERROR: [youtube] x: The uploader has not made this video available in your country",
     False, "region"),
    ("ERROR: [youtube] x: This live event will begin in 2 hours", False, "live"),
    ("ERROR: [youtube] x: Sign in to confirm you're not a bot", True, "cookies"),
    ("ERROR: unable to download video data: HTTP Error 429: Too Many Requests", True, "later"),
    ("ERROR: HTTP Error 503: Service Unavailable", True, "later"),
    ("ERROR: The read operation timed out", True, "later"),
    ("ERROR: ffprobe and ffmpeg not found. Please install", True, "ffmpeg not found"),
    ("ERROR: something weird happened", True, "something weird"),
])
def test_error_mapping(dest, msg, retryable, needle):
    dl, _ = make(fail_with(dl_error(msg)))
    with pytest.raises(StepError) as e:
        dl.download(VID, URL, dest, Meter())
    assert e.value.retryable is retryable
    assert needle in e.value.message.lower()
    assert listing(dest) == []  # no partial audio, no scratch folder


def test_ffmpeg_missing_message_exact():
    e = classify_error(FileNotFoundError("ffmpeg not found"))
    assert e.message == "ffmpeg not found, install it and retry" and e.retryable


def test_network_exception_is_retryable(dest):
    dl, _ = make(fail_with(socket.timeout("timed out")))
    with pytest.raises(StepError) as e:
        dl.download(VID, URL, dest, Meter())
    assert e.value.retryable


@pytest.mark.parametrize("info", [{"is_live": True}, {"live_status": "is_upcoming"}])
def test_live_info_not_retryable(dest, info):
    def b(opts, url):
        (Path(opts["outtmpl"]).parent / "audio.m4a").write_bytes(b"x")
        return {"title": "t", "duration": 5, **info}
    dl, _ = make(b)
    with pytest.raises(StepError) as e:
        dl.download(VID, URL, dest, Meter())
    assert e.value.retryable is False and "live stream" in e.value.message
    assert listing(dest) == []


def test_live_skipped_by_match_filter(dest):
    def b(opts, url):
        assert opts["match_filter"]({"live_status": "is_live"}) is not None
        assert opts["match_filter"]({"live_status": "not_live"}) is None
        return None
    dl, _ = make(b)
    with pytest.raises(StepError) as e:
        dl.download(VID, URL, dest, Meter())
    assert e.value.retryable is False and "live" in e.value.message


def test_no_file_produced(dest):
    dl, _ = make(good(write=False))
    with pytest.raises(StepError) as e:
        dl.download(VID, URL, dest, Meter())
    assert "no audio file" in e.value.message
    assert listing(dest) == []


@pytest.mark.parametrize("duration", [None, 0])
def test_no_duration(dest, duration):
    dl, _ = make(good(duration=duration))
    with pytest.raises(StepError) as e:
        dl.download(VID, URL, dest, Meter())
    assert e.value.retryable is False and "duration" in e.value.message
    assert listing(dest) == []


@pytest.mark.parametrize("exc", [KeyboardInterrupt(), ValueError("boom")])
def test_crash_and_interrupt_clean_up(dest, exc):
    dl, _ = make(fail_with(exc))
    with pytest.raises(type(exc)):
        dl.download(VID, URL, dest, Meter())
    assert listing(dest) == [] and not dest.exists()


def test_stale_scratch_is_replaced(dest):
    stale = dest.parent / WORK_DIR
    stale.mkdir()
    (stale / "old.part").write_bytes(b"old")
    dl, _ = make(good())
    dl.download(VID, URL, dest, Meter())
    assert listing(dest) == [dest.name]


def test_through_pipeline(tmp_path):
    db.bootstrap(tmp_path)
    dl, _ = make(good("Real title", 120))
    base = build_fakes(None, True)
    with closing(db.connect(tmp_path)) as c:
        submit.submit(c, URL)
    Worker(tmp_path, Adapters(dl, base.transcriber, base.summarizer, base.verifier),
           secrets=lambda: ()).run_next()
    with closing(db.connect(tmp_path)) as c:
        ep = episodes.get_episode(c, VID)
        j = episodes.get_latest_job_with_steps(c, VID)
    assert ep["title"] == "Real title" and ep["duration_seconds"] == 120
    assert j["steps"][0]["state"] == "done"
    folder = artifacts.episode_dir(tmp_path, VID)
    assert WORK_DIR not in os.listdir(folder)


@pytest.mark.parametrize("msg,retryable", [
    ("ERROR: Video unavailable. This content isn't available, try again later.", True),
    ("HTTP Error 503: Service Unavailable", True),
    ("ERROR: Private video. Sign in if you've been granted access to this video", False),
    ("ERROR: This video has been removed by the uploader", False),
    ("ERROR: Sign in to confirm you're not a bot", True),
    ("ERROR: Sign in to view this restricted item", True),
])
def test_classification_order(msg, retryable):
    assert classify_error(Exception(msg)).retryable is retryable


def test_disk_full_is_a_clear_retryable_error(dest):
    import errno

    def b(opts, url):
        raise OSError(errno.ENOSPC, "No space left on device")

    dl, _ = make(b)
    with pytest.raises(StepError) as e:
        dl.download(VID, URL, dest, Meter())
    assert e.value.retryable is True and "Disk write failed" in e.value.message
    assert "Network" not in e.value.message and listing(dest) == []


def test_unmapped_yt_dlp_error_becomes_step_error(dest):
    from yt_dlp.utils import ContentTooShortError

    def b(opts, url):
        raise ContentTooShortError(10, 5)

    dl, _ = make(b)
    with pytest.raises(StepError) as e:
        dl.download(VID, URL, dest, Meter())
    assert e.value.retryable is True
    assert listing(dest) == []


def test_empty_audio_file_is_not_published(dest):
    def b(opts, url):
        (Path(opts["outtmpl"]).parent / "audio.m4a").write_bytes(b"")
        return {"title": "T", "duration": 10}

    dl, _ = make(b)
    with pytest.raises(StepError, match="empty audio"):
        dl.download(VID, URL, dest, Meter())
    assert listing(dest) == []


@pytest.mark.parametrize("duration", [float("nan"), float("inf")])
def test_non_finite_duration_fails_before_publishing(dest, duration):
    dl, _ = make(good(duration=duration))
    with pytest.raises(StepError):
        dl.download(VID, URL, dest, Meter())
    assert listing(dest) == []  # nothing left at dest


def test_fake_config_matches_real_config_except_downloader():
    import tomllib
    root = Path(__file__).resolve().parent
    real = tomllib.loads((root.parent / "config.toml").read_text())
    fake = tomllib.loads((root / "fake_config.toml").read_text())
    assert fake["providers"]["downloader"] == "fake"
    real["providers"]["downloader"] = fake["providers"]["downloader"]
    assert real == fake


def test_network_is_blocked():
    with pytest.raises(RuntimeError, match="blocked"):
        socket.create_connection(("example.com", 80))


@pytest.mark.network
@pytest.mark.skipif(os.environ.get("RUN_NETWORK_TESTS") != "1",
                    reason="set RUN_NETWORK_TESTS=1 to download from YouTube")
def test_real_download(tmp_path):
    dest = tmp_path / "audio.m4a.tmp"
    r = YtDlpDownloader("node").download(
        "jNQXAC9IVRw", "https://www.youtube.com/watch?v=jNQXAC9IVRw", dest, Meter())
    assert dest.stat().st_size > 1000 and r.title and r.duration_seconds > 0
    assert sorted(p.name for p in tmp_path.iterdir()) == [dest.name]
