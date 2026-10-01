"""Start-up recovery of Jobs interrupted by a stop, crash or sleep (Story 3.1, AD-4, AD-5).

Call once at start-up, before the worker starts. Never call it while a worker is running:
it would reset a Job that is genuinely in progress.
"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from app.store import artifacts

log = logging.getLogger(__name__)


def recover(conn: sqlite3.Connection, data_dir: Path) -> int:
    """Requeue every `running` Job; return how many were recovered. Idempotent."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        rows = conn.execute(
            "SELECT id, video_id FROM jobs WHERE state = 'running' ORDER BY id").fetchall()
        for job_id, _ in rows:
            conn.execute("UPDATE steps SET state = 'pending', message = NULL, retryable = NULL "
                         "WHERE job_id = ? AND state = 'running'", (job_id,))
            conn.execute("UPDATE jobs SET state = 'queued' WHERE id = ?", (job_id,))
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    for video_id in {v for _, v in rows}:
        _remove_temp_files(data_dir, video_id)
    return len(rows)


def _remove_temp_files(data_dir: Path, video_id: str) -> None:
    try:
        folder = artifacts.episode_dir(data_dir, video_id)
        for p in folder.glob("*.tmp"):
            try:
                if p.is_file() or p.is_symlink():
                    p.unlink()
            except OSError:
                log.warning("could not remove a leftover temp file")
    except (OSError, ValueError):
        log.warning("could not clean up an Episode folder")
