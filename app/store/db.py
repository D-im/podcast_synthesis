"""Bootstrap data/ and SQLite (WAL). No tables yet."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from app.config import PROJECT_ROOT

DEFAULT_DATA_DIR = PROJECT_ROOT / "data"
DB_NAME = "podcast_synthesis.db"


def connect(data_dir: Path = DEFAULT_DATA_DIR) -> sqlite3.Connection:
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(data_dir / DB_NAME)
    mode = conn.execute("PRAGMA journal_mode=WAL").fetchone()[0]
    if str(mode).lower() != "wal":
        conn.close()
        raise RuntimeError(f"SQLite could not enable WAL mode (got '{mode}')")
    return conn


def bootstrap(data_dir: Path = DEFAULT_DATA_DIR) -> Path:
    """Create data/ and the DB in WAL mode; return the DB path."""
    conn = connect(data_dir)
    conn.close()
    return Path(data_dir) / DB_NAME
