"""Admit a new submission: free metadata lookup, whole-Job estimate, Daily Cap check (Story 3.3).

Nothing here writes to the database; the caller creates the Episode only on `Admitted`.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from app.config import ConfigError, load_config
from app.core.budget import check_budget, today_micro  # noqa: F401
from app.core.estimate import Prices, estimate_job
from app.core.meter import format_usd, to_micro
from app.core.submit import Rejected
from app.ports import DownloadResult, StepError

Lookup = Callable[[str, str], DownloadResult]   # (video_id, url) -> title and duration, free


@dataclass(frozen=True)
class Admitted:
    title: str
    duration_seconds: int
    estimate_micro: int


def admit(conn: sqlite3.Connection, video_id: str, url: str, lookup: Lookup,
          config_path: Path) -> Admitted | Rejected:
    try:
        found = lookup(video_id, url)
    except StepError as e:
        return Rejected(f"Could not look up that video ({e.message}). Nothing was created, "
                        "because the cost cannot be estimated without its length.", 409)
    except Exception:
        return Rejected("Could not look up that video just now. Nothing was created, because "
                        "the cost cannot be estimated without its length. Try again.", 409)
    try:
        config = load_config(config_path)
        prices = Prices.from_config(config)
        est = estimate_job(found.duration_seconds, prices)
        cap = to_micro(config.daily_cap_usd)
    except (ConfigError, KeyError, TypeError, ValueError, OSError):
        return Rejected("The config file could not be read, so the cost cannot be estimated. "
                        "Nothing was created.", 409)
    today = today_micro(conn)
    decision = check_budget(today, est.total_micro, cap)
    if not decision.allowed:
        return Rejected(
            f"Refused: {decision.reason}. Cap {format_usd(cap, 2)}, today's spend "
            f"{format_usd(today, 2)}, estimate for this Episode {format_usd(est.total_micro)}. "
            "Nothing was created.", 409)
    return Admitted(found.title, found.duration_seconds, est.total_micro)
