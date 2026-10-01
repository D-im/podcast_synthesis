"""Numbered migrations, tracked with PRAGMA user_version."""
from __future__ import annotations

import sqlite3

MIGRATIONS: list[list[str]] = [
    # 1: first tables
    [
        """CREATE TABLE episodes (
            video_id TEXT PRIMARY KEY,
            url TEXT NOT NULL,
            title TEXT,
            duration_seconds INTEGER,
            created_at TEXT NOT NULL
        )""",
        """CREATE TABLE jobs (
            id INTEGER PRIMARY KEY,
            video_id TEXT NOT NULL REFERENCES episodes(video_id),
            state TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )""",
        """CREATE TABLE steps (
            job_id INTEGER NOT NULL REFERENCES jobs(id),
            name TEXT NOT NULL,
            ordinal INTEGER NOT NULL,
            state TEXT NOT NULL,
            PRIMARY KEY (job_id, name)
        )""",
    ],
    # 2: failure details on steps
    [
        "ALTER TABLE steps ADD COLUMN message TEXT",
        "ALTER TABLE steps ADD COLUMN retryable INTEGER",
    ],
    # 3: append-only spend ledger (money in integer micro-dollars, UTC timestamps)
    [
        """CREATE TABLE spend_ledger (
            id INTEGER PRIMARY KEY,
            video_id TEXT NOT NULL REFERENCES episodes(video_id),
            step TEXT NOT NULL,
            provider TEXT NOT NULL,
            amount_micro_usd INTEGER NOT NULL,
            created_at TEXT NOT NULL
        )""",
        """CREATE INDEX spend_ledger_video ON spend_ledger(video_id)""",
        """CREATE INDEX spend_ledger_created ON spend_ledger(created_at)""",
        """CREATE TRIGGER spend_ledger_no_update BEFORE UPDATE ON spend_ledger
        BEGIN SELECT RAISE(ABORT, 'spend_ledger is append-only'); END""",
        """CREATE TRIGGER spend_ledger_no_delete BEFORE DELETE ON spend_ledger
        BEGIN SELECT RAISE(ABORT, 'spend_ledger is append-only'); END""",
    ],
    # 4: resumable vendor job on steps; idempotent ledger rows keyed by a vendor reference
    [
        "ALTER TABLE steps ADD COLUMN vendor_job_id TEXT",
        "ALTER TABLE spend_ledger ADD COLUMN provider_ref TEXT",
        """CREATE UNIQUE INDEX spend_ledger_ref
        ON spend_ledger(video_id, step, provider, provider_ref)
        WHERE provider_ref IS NOT NULL""",
    ],
    # 5: versioned One-Pagers (AD-14); the files live beside the Episode's other artifacts
    [
        """CREATE TABLE one_pager_versions (
            video_id TEXT NOT NULL REFERENCES episodes(video_id),
            version INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            model TEXT NOT NULL,
            prompt_hashes TEXT NOT NULL,
            PRIMARY KEY (video_id, version)
        )""",
    ],
    # 6: advisory accuracy score per Episode and One-Pager version (Story 2.1)
    [
        """CREATE TABLE fidelity_scores (
            video_id TEXT NOT NULL REFERENCES episodes(video_id),
            version INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            model TEXT NOT NULL,
            prompt_hashes TEXT NOT NULL,
            accuracy REAL NOT NULL,
            PRIMARY KEY (video_id, version)
        )""",
    ],
    # 7: advisory coverage score beside accuracy (Story 2.2); old rows keep NULL
    [
        "ALTER TABLE fidelity_scores ADD COLUMN coverage REAL",
    ],
    # 8: Verdicts, many per Episode, each on the One-Pager version it rated (Story 2.5)
    [
        """CREATE TABLE verdicts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            video_id TEXT NOT NULL,
            version INTEGER NOT NULL,
            rating TEXT NOT NULL CHECK (rating IN ('worth_it', 'not_worth_it')),
            reason TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (video_id, version) REFERENCES one_pager_versions(video_id, version)
        )""",
        "CREATE INDEX verdicts_episode ON verdicts(video_id, id)",
    ],
    # 9: how many times each step has started (Story 3.2)
    [
        "ALTER TABLE steps ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0",
    ],
]


def migrate(conn: sqlite3.Connection) -> None:
    """Apply pending migrations; each one is atomic with its version bump."""
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    if current > len(MIGRATIONS):
        raise RuntimeError(
            f"database schema version {current} is newer than this app knows "
            f"({len(MIGRATIONS)}); update the app or use a matching data folder"
        )
    for version, statements in enumerate(MIGRATIONS, start=1):
        if version <= current:
            continue
        conn.execute("BEGIN IMMEDIATE")
        try:
            # re-check under the write lock in case another process migrated
            if conn.execute("PRAGMA user_version").fetchone()[0] >= version:
                conn.execute("ROLLBACK")
                continue
            for stmt in statements:
                conn.execute(stmt)
            conn.execute(f"PRAGMA user_version = {version}")
            conn.execute("COMMIT")
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
