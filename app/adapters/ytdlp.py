"""yt-dlp adapter for the Downloader port: audio only, converted to m4a with ffmpeg."""
from __future__ import annotations

import errno
import math
import os
import re
import shutil
from pathlib import Path
from typing import Any, Callable

from app.ports import DownloadResult, Meter, StepError

WORK_DIR = ".ytdlp-work"
TITLE_LIMIT = 300
_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_DISK_ERRNOS = (errno.ENOSPC, errno.EACCES, errno.EROFS, errno.EDQUOT)
_HINT = "Update yt-dlp, try again later, or use browser cookies."

# (substring of the lowercased error, reason) -- permanent failures
_PERMANENT = (
    ("private video", "video is private"),
    ("members-only", "video is members-only"),
    ("members only", "video is members-only"),
    ("join this channel", "video is members-only"),
    ("confirm your age", "video is age-restricted"),
    ("age-restricted", "video is age-restricted"),
    ("age restricted", "video is age-restricted"),
    ("in your country", "video is blocked in your region"),
    ("not available in your", "video is blocked in your region"),
    ("live event", "video is a live stream or upcoming premiere"),
    ("live stream", "video is a live stream or upcoming premiere"),
    ("premieres in", "video is a live stream or upcoming premiere"),
    ("has been removed", "video was removed"),
    ("was removed", "video was removed"),
    ("video unavailable", "video is unavailable"),
    ("video is unavailable", "video is unavailable"),
    ("copyright claim", "video was removed for copyright"),
    ("terminated", "video was removed (account terminated)"),
    ("account associated", "video was removed (account terminated)"),
)


def _short(text: str, limit: int = 200) -> str:
    text = " ".join(_ANSI.sub("", str(text)).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _ydl_factory(opts: dict[str, Any]):
    import yt_dlp

    return yt_dlp.YoutubeDL(opts)


def classify_error(exc: BaseException) -> StepError:
    """Map a yt-dlp or network failure to a StepError with a readable reason."""
    text = _short(exc)
    low = text.lower()
    if "ffmpeg" in low or "ffprobe" in low:
        if "not found" in low or "not installed" in low or "no such file" in low:
            return StepError("ffmpeg not found, install it and retry", True)
    if isinstance(exc, OSError) and exc.errno in _DISK_ERRNOS:
        return StepError(f"Disk write failed ({_short(exc.strerror or exc)}). "
                         "Free space or fix permissions, then retry.", True)
    # A bot check is temporary, and so is anything that says to try again later; check these
    # before the permanent list so a transient "video unavailable, try again" is not final.
    if "not a bot" in low:
        return StepError(f"YouTube asked for a sign-in or bot check. {_HINT}", True)
    if isinstance(exc, (TimeoutError, ConnectionError)) or any(
        n in low for n in ("try again later", "temporarily", "timed out", "timeout", "429",
                           "too many requests", "http error 5", "connection", "network",
                           "name resolution", "temporary failure")
    ):
        return StepError(f"Network problem or rate limit talking to YouTube. {_HINT}", True)
    for needle, reason in _PERMANENT:
        if needle in low:
            return StepError(f"Cannot download: {reason}", False)
    if "sign in" in low:
        return StepError(f"YouTube asked for a sign-in or bot check. {_HINT}", True)
    return StepError(f"Download failed: {text}. {_HINT}", True)


class YtDlpDownloader:
    def __init__(self, js_runtime: str, ydl_factory: Callable[[dict], Any] | None = None):
        self.js_runtime = js_runtime
        self._factory = ydl_factory or _ydl_factory

    def download(self, video_id: str, url: str, dest: Path, meter: Meter) -> DownloadResult:
        # A download is free: nothing is recorded through the meter.
        dest = Path(dest)
        work = dest.parent / WORK_DIR
        shutil.rmtree(work, ignore_errors=True)
        work.mkdir(parents=True, exist_ok=True)
        try:
            return self._run(url, dest, work, video_id)
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def _run(self, url: str, dest: Path, work: Path, video_id: str) -> DownloadResult:
        skipped: list[str] = []

        def match_filter(info, *, incomplete=False):
            if info.get("is_live") or info.get("live_status") in ("is_live", "is_upcoming"):
                skipped.append("live")
                return "live stream"
            return None

        opts = {
            "format": "bestaudio/best",
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "ignoreconfig": True,
            "socket_timeout": 30,
            "retries": 3,
            "fragment_retries": 3,
            "extractor_retries": 2,
            "outtmpl": str(work / "audio.%(ext)s"),
            "match_filter": match_filter,
            "js_runtimes": {self.js_runtime: {}},
            "postprocessors": [
                {"key": "FFmpegExtractAudio", "preferredcodec": "m4a"},
            ],
        }
        try:
            with self._factory(opts) as ydl:
                info = ydl.extract_info(url, download=True)
        except StepError:
            raise
        except Exception as e:
            if _is_yt_dlp_or_network_error(e):
                raise classify_error(e) from None
            raise
        if skipped or (info and (info.get("is_live")
                                 or info.get("live_status") in ("is_live", "is_upcoming"))):
            raise StepError("Cannot download: video is a live stream or upcoming premiere", False)
        if not info:
            raise StepError("yt-dlp finished without returning video information", True)
        duration = info.get("duration")
        if (not isinstance(duration, (int, float)) or isinstance(duration, bool)
                or not math.isfinite(duration) or duration <= 0):
            raise StepError("yt-dlp returned no duration for this video", False)
        seconds = int(round(duration))  # computed before the file is published
        produced = work / "audio.m4a"
        if not produced.is_file():
            found = sorted(p for p in work.glob("*.m4a") if p.is_file())
            if len(found) != 1:
                raise StepError("yt-dlp finished but produced no audio file", True)
            produced = found[0]
        if produced.stat().st_size == 0:
            raise StepError("yt-dlp produced an empty audio file", True)
        os.replace(produced, dest)
        title = " ".join(str(info.get("title") or "").split())[:TITLE_LIMIT] or video_id
        return DownloadResult(title, seconds)


def _is_yt_dlp_or_network_error(e: Exception) -> bool:
    if isinstance(e, OSError):  # sockets, timeouts, missing ffmpeg executable
        return True
    try:
        from yt_dlp.utils import YoutubeDLError
    except ImportError:
        return False
    return isinstance(e, YoutubeDLError)
