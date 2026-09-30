"""Single place that turns a YouTube URL into a video ID."""
from __future__ import annotations

import re
from urllib.parse import parse_qs, urlsplit

_ID = re.compile(r"[A-Za-z0-9_-]{11}")
_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"}
_PATH_PREFIXES = ("shorts", "embed", "live")
_HAS_SCHEME = re.compile(r"[a-z][a-z0-9+.-]*://", re.I)
_SCHEMELESS = re.compile(r"(?:www\.|m\.|music\.)?(?:youtube\.com|youtu\.be)(?:[/?#]|$)", re.I)


def _valid(candidate: str | None) -> str | None:
    if candidate and _ID.fullmatch(candidate):
        return candidate
    return None


def parse_video_id(url: str) -> str | None:
    if not isinstance(url, str):
        return None
    url = url.strip()
    if not _HAS_SCHEME.match(url) and _SCHEMELESS.match(url):
        url = "https://" + url  # a link pasted without its scheme
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
    except ValueError:
        return None
    if parts.scheme.lower() not in ("http", "https"):
        return None
    segments = [s for s in parts.path.split("/") if s]
    if host == "youtu.be":
        return _valid(segments[0]) if segments else None
    if host in _HOSTS:
        if parts.path == "/watch":
            values = parse_qs(parts.query).get("v", [])
            return _valid(values[0]) if values else None
        if len(segments) >= 2 and segments[0] in _PATH_PREFIXES:
            return _valid(segments[1])
    return None
