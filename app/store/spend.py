"""All SQL for the append-only spend ledger. Only app/core/meter.py may call `append`."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

STAMP_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"


def utc_stamp(moment: datetime | None = None) -> str:
    """UTC timestamp in one fixed format, so text comparison orders correctly."""
    moment = moment or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return moment.astimezone(timezone.utc).strftime(STAMP_FORMAT)


def append(conn: sqlite3.Connection, video_id: str, step: str, provider: str,
           amount_micro_usd: int, created_at: datetime | None = None) -> None:
    conn.execute(
        "INSERT INTO spend_ledger (video_id, step, provider, amount_micro_usd, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (video_id, step, provider, amount_micro_usd, utc_stamp(created_at)),
    )


def total_for_episode(conn: sqlite3.Connection, video_id: str) -> int:
    return conn.execute(
        "SELECT COALESCE(SUM(amount_micro_usd), 0) FROM spend_ledger WHERE video_id = ?",
        (video_id,),
    ).fetchone()[0]


def by_step(conn: sqlite3.Connection, video_id: str) -> dict[str, int]:
    rows = conn.execute(
        "SELECT step, SUM(amount_micro_usd) FROM spend_ledger WHERE video_id = ? GROUP BY step",
        (video_id,),
    ).fetchall()
    return {step: total for step, total in rows}


def total_between(conn: sqlite3.Connection, start_utc: datetime, end_utc: datetime) -> int:
    """Sum of rows with start <= created_at < end."""
    return conn.execute(
        "SELECT COALESCE(SUM(amount_micro_usd), 0) FROM spend_ledger "
        "WHERE created_at >= ? AND created_at < ?",
        (utc_stamp(start_utc), utc_stamp(end_utc)),
    ).fetchone()[0]
