from __future__ import annotations

import sqlite3
import sys
from contextlib import closing

import uvicorn

from app import checks, env
from app.config import DEFAULT_CONFIG_PATH, ConfigError, load_config
from app.core.recovery import recover
from app.store import db
from app.web.app import create_app
from app.web.flags import Thresholds
from app.worker import Worker, build_adapters

HOST = "127.0.0.1"  # fixed: localhost only, no auth


def collect_warnings(environ=None, path: str | None = None) -> list[str]:
    warnings = [f"Missing API key: {k}" for k in env.missing_keys(environ)]
    warnings += [t.describe() for t in checks.check_tools(path)]
    return warnings


def run() -> None:
    try:
        config = load_config()
    except ConfigError as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        raise SystemExit(1)
    env.load_env()
    try:
        db.bootstrap()
    except (OSError, sqlite3.Error, RuntimeError) as e:
        print(f"Startup error: cannot set up the data folder and database ({e})", file=sys.stderr)
        raise SystemExit(1) from None
    warnings = collect_warnings()
    for w in warnings:
        print(f"WARNING: {w}")
    try:
        adapters = build_adapters(config)
    except ValueError as e:
        print(f"Configuration error: {e}", file=sys.stderr)
        raise SystemExit(1) from None
    try:
        with closing(db.connect(db.DEFAULT_DATA_DIR)) as conn:
            recovered = recover(conn, db.DEFAULT_DATA_DIR)
    except (OSError, sqlite3.Error, RuntimeError) as e:
        print(f"Startup error: cannot recover interrupted Jobs ({e})", file=sys.stderr)
        raise SystemExit(1) from None
    if recovered:
        print(f"Recovered {recovered} interrupted Job(s)")
    worker = Worker(db.DEFAULT_DATA_DIR, adapters)
    worker.start()
    try:
        app = create_app(
            warnings, db.DEFAULT_DATA_DIR, config.daily_cap_usd, DEFAULT_CONFIG_PATH,
            Thresholds(config.accuracy_threshold, config.coverage_threshold))
        uvicorn.run(app, host=HOST, port=config.port)
    finally:
        worker.stop()
