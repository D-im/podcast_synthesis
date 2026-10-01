"""All SQL for episodes, jobs and steps."""
from __future__ import annotations

import json
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
    conn: sqlite3.Connection, video_id: str, url: str, step_names: Sequence[str],
    title: str | None = None, duration_seconds: int | None = None,
    estimate_micro: int | None = None,
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
            "INSERT INTO episodes (video_id, url, title, duration_seconds, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (video_id, url, title, duration_seconds, now),
        )
        job_id = conn.execute(
            "INSERT INTO jobs (video_id, state, created_at, updated_at, estimate_micro) "
            "VALUES (?, 'queued', ?, ?, ?)",
            (video_id, now, now, estimate_micro),
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
        "SELECT id, state, created_at, updated_at, estimate_micro FROM jobs "
        "WHERE video_id = ? ORDER BY id DESC LIMIT 1",
        (video_id,),
    ).fetchone()
    if job is None:
        return None
    steps = conn.execute(
        "SELECT name, ordinal, state, message, retryable, attempts FROM steps "
        "WHERE job_id = ? ORDER BY ordinal",
        (job[0],),
    ).fetchall()
    return {
        "id": job[0],
        "state": job[1],
        "created_at": job[2],
        "updated_at": job[3],
        "estimate_micro": job[4],
        "steps": [
            {"name": n, "ordinal": o, "state": s, "message": m, "retryable": r, "attempts": a}
            for n, o, s, m, r, a in steps
        ],
    }


def next_queued_job(conn: sqlite3.Connection) -> dict | None:
    """Oldest queued Job (by id), with its Episode URL."""
    row = conn.execute(
        "SELECT j.id, j.video_id, e.url FROM jobs j JOIN episodes e USING (video_id) "
        "WHERE j.state = 'queued' ORDER BY j.id LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    return {"id": row[0], "video_id": row[1], "url": row[2]}


def set_job_state(conn: sqlite3.Connection, job_id: int, state: str) -> None:
    conn.execute(
        "UPDATE jobs SET state = ?, updated_at = ? WHERE id = ?", (state, _now(), job_id)
    )


def get_steps(conn: sqlite3.Connection, job_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT name, ordinal, state, message, retryable, attempts FROM steps "
        "WHERE job_id = ? ORDER BY ordinal",
        (job_id,),
    ).fetchall()
    return [
        {"name": n, "ordinal": o, "state": s, "message": m, "retryable": r, "attempts": a}
        for n, o, s, m, r, a in rows
    ]


def set_step_state(
    conn: sqlite3.Connection, job_id: int, name: str, state: str,
    message: str | None = None, retryable: bool | None = None,
) -> None:
    conn.execute(
        "UPDATE steps SET state = ?, message = ?, retryable = ?, "
        "attempts = attempts + ? WHERE job_id = ? AND name = ?",
        (state, message, None if retryable is None else int(retryable),
         1 if state == "running" else 0, job_id, name),
    )


def set_episode_metadata(
    conn: sqlite3.Connection, video_id: str, title: str, duration_seconds: int
) -> None:
    conn.execute(
        "UPDATE episodes SET title = ?, duration_seconds = ? WHERE video_id = ?",
        (title, duration_seconds, video_id),
    )


def get_vendor_job_id(conn: sqlite3.Connection, job_id: int, name: str) -> str | None:
    row = conn.execute(
        "SELECT vendor_job_id FROM steps WHERE job_id = ? AND name = ?", (job_id, name)
    ).fetchone()
    return row[0] if row else None


def set_vendor_job_id(
    conn: sqlite3.Connection, job_id: int, name: str, vendor_job_id: str | None
) -> None:
    cur = conn.execute(
        "UPDATE steps SET vendor_job_id = ? WHERE job_id = ? AND name = ?",
        (vendor_job_id, job_id, name),
    )
    if cur.rowcount != 1:  # a silent no-op would lose the job ID and risk paying twice
        raise LookupError(f"step {name} of job {job_id} not found")


def latest_one_pager_version(conn: sqlite3.Connection, video_id: str) -> dict | None:
    row = conn.execute(
        "SELECT version, created_at, model, prompt_hashes FROM one_pager_versions "
        "WHERE video_id = ? ORDER BY version DESC LIMIT 1",
        (video_id,),
    ).fetchone()
    if row is None:
        return None
    return {"version": row[0], "created_at": row[1], "model": row[2],
            "prompt_hashes": json.loads(row[3])}


def add_one_pager_version(
    conn: sqlite3.Connection, video_id: str, version: int, model: str,
    prompt_hashes: dict[str, str],
) -> None:
    conn.execute(
        "INSERT INTO one_pager_versions (video_id, version, created_at, model, prompt_hashes) "
        "VALUES (?, ?, ?, ?, ?)",
        (video_id, version, _now(), model, json.dumps(prompt_hashes, sort_keys=True)),
    )


def add_fidelity_score(
    conn: sqlite3.Connection, video_id: str, version: int, model: str,
    prompt_hashes: dict[str, str], accuracy: float, coverage: float | None = None,
) -> None:
    """Store the score for one One-Pager version (a re-run of the same version replaces it)."""
    conn.execute(
        "INSERT OR REPLACE INTO fidelity_scores "
        "(video_id, version, created_at, model, prompt_hashes, accuracy, coverage) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (video_id, version, _now(), model, json.dumps(prompt_hashes, sort_keys=True),
         accuracy, coverage),
    )


def latest_fidelity_score(conn: sqlite3.Connection, video_id: str) -> dict | None:
    row = conn.execute(
        "SELECT version, created_at, model, prompt_hashes, accuracy, coverage FROM fidelity_scores "
        "WHERE video_id = ? ORDER BY version DESC LIMIT 1",
        (video_id,),
    ).fetchone()
    if row is None:
        return None
    return {"version": row[0], "created_at": row[1], "model": row[2],
            "prompt_hashes": json.loads(row[3]), "accuracy": row[4], "coverage": row[5]}


def list_episodes(conn: sqlite3.Connection, limit: int | None = None) -> list[dict]:
    """Episodes newest first, each with its latest Job (and steps) and latest One-Pager version.

    Each item: the `get_episode` keys plus `job` (as `get_latest_job_with_steps`, or None)
    `one_pager_version` (int or None) and `fidelity_accuracy` and `fidelity_coverage` (scores of the latest One-Pager version, or None).
    """
    sql = (
        "SELECT e.video_id, e.url, e.title, e.duration_seconds, e.created_at, "
        "(SELECT MAX(version) FROM one_pager_versions o WHERE o.video_id = e.video_id), "
        "(SELECT accuracy FROM fidelity_scores f WHERE f.video_id = e.video_id "
        "AND f.version = (SELECT MAX(version) FROM one_pager_versions o2 "
        "WHERE o2.video_id = e.video_id)), "
        "(SELECT coverage FROM fidelity_scores f WHERE f.video_id = e.video_id "
        "AND f.version = (SELECT MAX(version) FROM one_pager_versions o2 "
        "WHERE o2.video_id = e.video_id)), "
        "(SELECT rating FROM verdicts v WHERE v.video_id = e.video_id ORDER BY v.id DESC LIMIT 1), "
        "(SELECT version FROM verdicts v WHERE v.video_id = e.video_id ORDER BY v.id DESC LIMIT 1) "
        "FROM episodes e ORDER BY e.created_at DESC, e.rowid DESC"
    )
    params: tuple = ()
    if limit is not None:
        sql += " LIMIT ?"
        params = (int(limit),)
    rows = conn.execute(sql, params).fetchall()
    keys = ("video_id", "url", "title", "duration_seconds", "created_at")
    items = [dict(zip(keys, r[:5]), one_pager_version=r[5],
                  fidelity_accuracy=r[6], fidelity_coverage=r[7],
                  verdict_rating=r[8], verdict_version=r[9], job=None) for r in rows]
    if not items:
        return items
    by_id = {i["video_id"]: i for i in items}
    jobs = conn.execute(
        "SELECT j.video_id, j.id, j.state, j.created_at, j.updated_at FROM jobs j "
        "WHERE j.id = (SELECT MAX(id) FROM jobs k WHERE k.video_id = j.video_id)"
    ).fetchall()
    job_by_id = {}
    for video_id, jid, state, created, updated in jobs:
        if video_id in by_id:
            job = {"id": jid, "state": state, "created_at": created,
                   "updated_at": updated, "steps": []}
            by_id[video_id]["job"] = job
            job_by_id[jid] = job
    steps = conn.execute(
        "SELECT job_id, name, ordinal, state, message, retryable FROM steps ORDER BY ordinal"
    ).fetchall()
    for jid, n, o, s, m, r in steps:
        if jid in job_by_id:
            job_by_id[jid]["steps"].append(
                {"name": n, "ordinal": o, "state": s, "message": m, "retryable": r})
    return items


ACTIVE_JOB_STATES = ("queued", "running", "paused")


def has_active_job(conn: sqlite3.Connection, video_id: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM jobs WHERE video_id = ? AND state IN (?, ?, ?) LIMIT 1",
        (video_id, *ACTIVE_JOB_STATES),
    ).fetchone() is not None


def enqueue_regeneration(conn: sqlite3.Connection, video_id: str,
                         step_names: Sequence[str], done_steps: Sequence[str],
                         estimate_micro: int | None = None) -> int | None:
    """Queue a Job whose `done_steps` are stored `done` and the rest `pending`, atomically.

    Re-checks inside the transaction that no Job is active for the Episode; returns the new
    Job id, or None (nothing written) if one is active or the Episode does not exist.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        if get_episode(conn, video_id) is None or has_active_job(conn, video_id):
            conn.execute("ROLLBACK")
            return None
        now = _now()
        job_id = conn.execute(
            "INSERT INTO jobs (video_id, state, created_at, updated_at, estimate_micro) "
            "VALUES (?, 'queued', ?, ?, ?)",
            (video_id, now, now, estimate_micro),
        ).lastrowid
        for ordinal, name in enumerate(step_names, start=1):
            conn.execute(
                "INSERT INTO steps (job_id, name, ordinal, state) VALUES (?, ?, ?, ?)",
                (job_id, name, ordinal, "done" if name in done_steps else "pending"),
            )
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    return job_id


def list_one_pager_versions(conn: sqlite3.Connection, video_id: str) -> list[dict]:
    """All versions, oldest first."""
    rows = conn.execute(
        "SELECT version, created_at, model, prompt_hashes FROM one_pager_versions "
        "WHERE video_id = ? ORDER BY version",
        (video_id,),
    ).fetchall()
    out = []
    for version, created, model, hashes in rows:
        try:
            parsed = json.loads(hashes)
            if not isinstance(parsed, dict):
                parsed = {}
        except (ValueError, TypeError):
            parsed = {}
        out.append({"version": version, "created_at": created, "model": model,
                    "prompt_hashes": parsed})
    return out


def add_verdict(conn: sqlite3.Connection, video_id: str, version: int, rating: str,
                reason: str) -> int | None:
    """Store a Verdict on an existing One-Pager version; None (nothing written) if it is missing."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        exists = conn.execute(
            "SELECT 1 FROM one_pager_versions WHERE video_id = ? AND version = ?",
            (video_id, version)).fetchone()
        if exists is None:
            conn.execute("ROLLBACK")
            return None
        verdict_id = conn.execute(
            "INSERT INTO verdicts (video_id, version, rating, reason, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (video_id, version, rating, reason, _now())).lastrowid
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    return verdict_id


def list_verdicts(conn: sqlite3.Connection, video_id: str) -> list[dict]:
    """All Verdicts for an Episode, newest first."""
    rows = conn.execute(
        "SELECT id, version, rating, reason, created_at FROM verdicts "
        "WHERE video_id = ? ORDER BY id DESC", (video_id,)).fetchall()
    return [{"id": r[0], "version": r[1], "rating": r[2], "reason": r[3], "created_at": r[4]}
            for r in rows]


def retry_failed_job(conn: sqlite3.Connection, video_id: str) -> str:
    """Requeue the Episode's latest failed Job from its retryable failed step, atomically.

    Returns `queued`, or why nothing was written: `no_episode`, `no_job`, `not_failed`,
    `not_retryable`, `active`.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        outcome = "queued"
        job = None
        if get_episode(conn, video_id) is None:
            outcome = "no_episode"
        else:
            job = get_latest_job_with_steps(conn, video_id)
            if job is None:
                outcome = "no_job"
            elif has_active_job(conn, video_id):
                outcome = "active"
            elif job["state"] != "failed":
                outcome = "not_failed"
            else:
                failed = next((s for s in job["steps"] if s["state"] == "failed"), None)
                if failed is None or failed["retryable"] != 1:
                    outcome = "not_retryable"
        if outcome != "queued":
            conn.execute("ROLLBACK")
            return outcome
        conn.execute(
            "UPDATE steps SET state = 'pending', message = NULL, retryable = NULL "
            "WHERE job_id = ? AND name = ?", (job["id"], failed["name"]))
        conn.execute("UPDATE jobs SET state = 'queued', updated_at = ? WHERE id = ?",
                     (_now(), job["id"]))
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    return "queued"
