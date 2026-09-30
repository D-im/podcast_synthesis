"""All SQL for episodes, jobs and steps."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Sequence


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_episode(conn: sqlite3.Connection, video_id: str) -> dict | None:
    row = conn.execute(
        "SELECT video_id, url, title, duration_seconds, created_at "
        "FROM episodes WHERE video_id = ?",
        (video_id,),
    ).fetchone()
    if row is None:
        return None
    keys = ("video_id", "url", "title", "duration_seconds", "created_at")
    return dict(zip(keys, row))


def create_episode_with_job(
    conn: sqlite3.Connection, video_id: str, url: str, step_names: Sequence[str]
) -> tuple[dict, bool]:
    """Create Episode + queued Job + pending steps atomically.

    Returns (episode, created). If the Episode exists, nothing is written.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        existing = get_episode(conn, video_id)
        if existing is not None:
            conn.execute("ROLLBACK")
            return existing, False
        now = _now()
        conn.execute(
            "INSERT INTO episodes (video_id, url, created_at) VALUES (?, ?, ?)",
            (video_id, url, now),
        )
        job_id = conn.execute(
            "INSERT INTO jobs (video_id, state, created_at, updated_at) "
            "VALUES (?, 'queued', ?, ?)",
            (video_id, now, now),
        ).lastrowid
        for ordinal, name in enumerate(step_names, start=1):
            conn.execute(
                "INSERT INTO steps (job_id, name, ordinal, state) "
                "VALUES (?, ?, ?, 'pending')",
                (job_id, name, ordinal),
            )
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    episode = get_episode(conn, video_id)
    if episode is None:
        raise RuntimeError(f"episode {video_id} missing after create")
    return episode, True


def get_latest_job_with_steps(conn: sqlite3.Connection, video_id: str) -> dict | None:
    job = conn.execute(
        "SELECT id, state, created_at, updated_at FROM jobs "
        "WHERE video_id = ? ORDER BY id DESC LIMIT 1",
        (video_id,),
    ).fetchone()
    if job is None:
        return None
    steps = conn.execute(
        "SELECT name, ordinal, state FROM steps WHERE job_id = ? ORDER BY ordinal",
        (job[0],),
    ).fetchall()
    return {
        "id": job[0],
        "state": job[1],
        "created_at": job[2],
        "updated_at": job[3],
        "steps": [{"name": n, "ordinal": o, "state": s} for n, o, s in steps],
    }
