"""Regenerate a One-Pager: preconditions, estimate and enqueue. Never runs or spends anything."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from app.config import Config
from app.core.chunking import estimate_tokens
from app.core.estimate import Estimate, Prices, estimate_regeneration
from app.core.submit import STEP_NAMES
from app.core.text import transcript_text
from app.ports import OnePager, Transcript
from app.store import artifacts, episodes

DONE_STEPS = ("download", "transcribe")


class UnknownEpisode(Exception):
    pass


class Blocked(Exception):
    """A precondition failed (HTTP 409); `message` is safe to show."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def check_preconditions(conn: sqlite3.Connection, data_dir: Path, video_id: str) -> None:
    if episodes.get_episode(conn, video_id) is None:
        raise UnknownEpisode(video_id)
    if episodes.has_active_job(conn, video_id):
        raise Blocked("This Episode already has a Job waiting or running. "
                      "Regenerate is available again when it finishes.")
    try:
        have = artifacts.exists(artifacts.artifact_path(data_dir, video_id, artifacts.TRANSCRIPT))
    except ValueError:
        have = False
    if not have:
        raise Blocked("This Episode has no stored Transcript, so it cannot be regenerated.")


def can_regenerate(conn: sqlite3.Connection, data_dir: Path, video_id: str) -> bool:
    try:
        check_preconditions(conn, data_dir, video_id)
    except (UnknownEpisode, Blocked):
        return False
    return True


def _one_pager_chars(sections) -> int:
    return len("\n\n".join(f"## {s.name}\n{s.text}" for s in sections))


def estimate_for_episode(conn: sqlite3.Connection, data_dir: Path, video_id: str,
                         config: Config) -> Estimate:
    """Estimate from the stored Transcript and latest One-Pager and the config's prices."""
    try:
        transcript = Transcript.from_dict(artifacts.read_json(
            artifacts.artifact_path(data_dir, video_id, artifacts.TRANSCRIPT)))
        transcript_tokens = estimate_tokens(transcript_text(transcript))
        if not transcript.segments or transcript_tokens == 0:
            raise Blocked("The stored Transcript is empty, so it cannot be regenerated.")
    except Blocked:
        raise
    except (ValueError, OSError, KeyError, TypeError, RecursionError):
        raise Blocked("The stored Transcript could not be read, so it cannot be regenerated.") \
            from None
    prices = Prices.from_config(config)
    page_tokens = prices.anthropic_max_output_tokens  # no readable One-Pager: assume a full one
    latest = episodes.latest_one_pager_version(conn, video_id)
    if latest is not None:
        try:
            page = OnePager.from_dict(artifacts.read_json(
                artifacts.one_pager_path(data_dir, video_id, latest["version"])))
            page_tokens = estimate_tokens("x" * _one_pager_chars(page.sections))
        except (ValueError, OSError, KeyError, TypeError, RecursionError):
            pass
    return estimate_regeneration(transcript_tokens, page_tokens, prices)


def enqueue(conn: sqlite3.Connection, data_dir: Path, video_id: str) -> int:
    """Queue the regeneration Job. Raises UnknownEpisode or Blocked; nothing is written then."""
    check_preconditions(conn, data_dir, video_id)
    job_id = episodes.enqueue_regeneration(conn, video_id, STEP_NAMES, DONE_STEPS)
    if job_id is None:  # lost a race with another confirm
        check_preconditions(conn, data_dir, video_id)
        raise Blocked("This Episode already has a Job waiting or running.")
    return job_id


def change_summary(previous: dict | None, current: dict) -> str:
    """One line: what changed in the prompts and model since the previous version."""
    if previous is None:
        return "first version"
    cur, prev = current.get("prompt_hashes") or {}, previous.get("prompt_hashes") or {}
    model = (f"model changed: {previous.get('model')} to {current.get('model')}"
             if current.get("model") != previous.get("model") else "")
    if not cur or not prev:     # a missing record is not a change in the prompts
        return "; ".join(p for p in ("no prompt record", model) if p)
    changed = sorted(k for k in cur if k in prev and cur[k] != prev[k])
    added = sorted(k for k in cur if k not in prev)
    removed = sorted(k for k in prev if k not in cur)
    parts = []
    if changed:
        parts.append("changed: " + ", ".join(changed))
    if added:
        parts.append("added: " + ", ".join(added))
    if removed:
        parts.append("removed: " + ", ".join(removed))
    if model:
        parts.append(model)
    return "; ".join(parts) or "no prompt change"


def history(versions: list[dict]) -> list[dict]:
    """Versions (oldest first) as rows newest first, each with its change summary."""
    rows = []
    for i, v in enumerate(versions):
        rows.append({**v, "summary": change_summary(versions[i - 1] if i else None, v)})
    return rows[::-1]
