"""Library list and plain-text search over Episodes. No HTTP here."""
from __future__ import annotations

import math
import re
import sqlite3
from datetime import datetime
from pathlib import Path

from app.ports import OnePager
from app.store import artifacts, episodes
from app.web.status import describe_job

DEFAULT_LIMIT = 100
SEARCH_LIMIT = 200
MAX_QUERY_CHARS = 200
SNIPPET_RADIUS = 60


def parse_query(raw: str | None) -> str:
    return (raw or "").strip()[:MAX_QUERY_CHARS].strip()


def local_datetime(created_at: str) -> str:
    try:
        return datetime.fromisoformat(created_at).astimezone().strftime("%Y-%m-%d %H:%M")
    except (ValueError, TypeError, OverflowError, OSError):
        return created_at or ""


def percent(accuracy) -> str | None:
    """0.8333 -> '83.3%'. None for anything that is not a number from 0 to 1."""
    if isinstance(accuracy, bool) or not isinstance(accuracy, (int, float)):
        return None
    if not 0 <= accuracy <= 1:
        return None
    shown = math.floor(accuracy * 1000) / 10      # never rounds 99.99 up to 100
    return f"{shown:g}%"


def _row(item: dict, snippet: str | None = None) -> dict:
    status = describe_job(item, item.get("job"))
    return {
        "video_id": item["video_id"],
        "title": item.get("title") or item["video_id"],
        "date": local_datetime(item["created_at"]),
        "status": status["label"],
        "verdict": None,   # filled by Epic 2
        "fidelity": percent(item.get("fidelity_accuracy")),
        "snippet": snippet,
    }


def _one_pager_text(data_dir: Path, item: dict) -> str:
    version = item.get("one_pager_version")
    if version is None:
        return ""
    try:
        page = OnePager.from_dict(
            artifacts.read_json(artifacts.one_pager_path(data_dir, item["video_id"], version)))
        return "\n".join(s.text for s in page.sections if isinstance(s.text, str))
    except (ValueError, OSError, KeyError, TypeError, AttributeError, RecursionError):
        return ""  # unreadable: this Episode can still match by title


def _snippet(text: str, patterns: list[re.Pattern]) -> str | None:
    # offsets come from the original text, so characters whose case folding changes length
    # (like the German sharp s) cannot shift the snippet
    hits = [m.start() for m in (p.search(text) for p in patterns) if m]
    if not hits:
        return None
    pos = min(hits)
    start = max(0, pos - SNIPPET_RADIUS)
    end = min(len(text), pos + SNIPPET_RADIUS)
    piece = " ".join(text[start:end].split())
    return ("..." if start > 0 else "") + piece + ("..." if end < len(text) else "")


def build_library(conn: sqlite3.Connection, data_dir: Path, raw_query: str | None) -> dict:
    """Return {query, rows, truncated}. Blank query gives the latest 100; else search all."""
    query = parse_query(raw_query)
    if not query:
        items = episodes.list_episodes(conn, DEFAULT_LIMIT)
        return {"query": "", "rows": [_row(i) for i in items], "truncated": False}
    patterns = [re.compile(re.escape(w), re.IGNORECASE) for w in query.split()]
    rows: list[dict] = []
    truncated = False
    for item in episodes.list_episodes(conn):
        title = item.get("title") or item["video_id"]
        text = None
        ok = True
        for pat in patterns:
            if pat.search(title):
                continue
            if text is None:
                text = _one_pager_text(data_dir, item)
            if not pat.search(text):
                ok = False
                break
        if not ok:
            continue
        if len(rows) >= SEARCH_LIMIT:
            truncated = True
            break
        if text is None:
            text = _one_pager_text(data_dir, item)
        rows.append(_row(item, _snippet(text, patterns) if text else None))
    return {"query": query, "rows": rows, "truncated": truncated}
