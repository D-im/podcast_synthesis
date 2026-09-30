"""Bootstrap data/ and SQLite (WAL), connection PRAGMAs, and migrations."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from app.config import PROJECT_ROOT
from app.store.migrations import migrate

DEFAULT_DATA_DIR = PROJECT_ROOT / "data"
DB_NAME = "podcast_synthesis.db"
BUSY_TIMEOUT_MS = 5000


def connect(data_dir: Path = DEFAULT_DATA_DIR) -> sqlite3.Connection:
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    # isolation_level=None: explicit transaction control (BEGIN/COMMIT by hand)
    conn = sqlite3.connect(
        data_dir / DB_NAME, timeout=BUSY_TIMEOUT_MS / 1000, isolation_level=None
    )
    conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA foreign_keys=ON")
    mode = conn.execute("PRAGMA journal_mode=WAL").fetchone()[0]
    if str(mode).lower() != "wal":
        conn.close()
        raise RuntimeError(f"SQLite could not enable WAL mode (got '{mode}')")
    return conn


def bootstrap(data_dir: Path = DEFAULT_DATA_DIR) -> Path:
    """Create data/ and the DB in WAL mode, apply migrations; return the DB path."""
    conn = connect(data_dir)
    try:
        migrate(conn)
    finally:
        conn.close()
    return Path(data_dir) / DB_NAME
