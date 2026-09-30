"""Submission orchestration: parse, then create-or-find the Episode."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from app.core.youtube import parse_video_id
from app.store import episodes

STEP_NAMES = ("download", "transcribe", "summarize", "verify")

INVALID_URL_MESSAGE = (
    "That doesn't look like a valid YouTube video link. "
    "Use a watch, shorts, embed, live or youtu.be link."
)


@dataclass(frozen=True)
class Submitted:
    video_id: str
    created: bool


@dataclass(frozen=True)
class Rejected:
    reason: str


def canonical_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


def submit(conn: sqlite3.Connection, url: str) -> Submitted | Rejected:
    video_id = parse_video_id(url)
    if video_id is None:
        return Rejected(INVALID_URL_MESSAGE)
    episode, created = episodes.create_episode_with_job(
        conn, video_id, canonical_url(video_id), STEP_NAMES
    )
    return Submitted(episode["video_id"], created)
