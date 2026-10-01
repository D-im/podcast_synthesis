"""The budget guard for paid steps (AD-8): the single place that decides whether a Job may start
the next paid step. Used by the pipeline before each paid step and by Resume."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from app.config import ConfigError, load_config
from app.core import regenerate as core_regen
from app.core.budget import BudgetDecision, check_budget, today_micro
from app.core.estimate import Prices, estimate_job
from app.core.meter import to_micro
from app.ports import StepError
from app.store import episodes

PAID_STEPS = ("transcribe", "summarize", "verify")


def step_estimate(conn: sqlite3.Connection, data_dir: Path, video_id: str, step: str,
                  config) -> int:
    """Estimated micro-dollars for one step from stored data; 0 when it costs nothing."""
    if step == "transcribe":
        episode = episodes.get_episode(conn, video_id) or {}
        return estimate_job(episode.get("duration_seconds") or 0,
                            Prices.from_config(config)).transcribe_micro
    if step not in ("summarize", "verify"):
        return 0
    try:
        est = core_regen.estimate_for_episode(conn, data_dir, video_id, config)
    except core_regen.Blocked:
        return 0          # nothing readable to size: the step itself will report the problem
    return est.summarize_micro if step == "summarize" else est.verify_micro


def check_step(conn: sqlite3.Connection, data_dir: Path, video_id: str, step: str,
               config_path: Path) -> BudgetDecision:
    """May this Episode start `step` now? A step with no cost is always allowed.

    Raises StepError when the config cannot be read (nothing is spent in that case).
    """
    try:
        config = load_config(config_path)
        estimate = step_estimate(conn, data_dir, video_id, step, config)
        cap = to_micro(config.daily_cap_usd)
    except (ConfigError, KeyError, TypeError, ValueError, OSError):
        raise StepError("config.toml could not be read, so the Daily Cap cannot be checked; "
                        "fix it and retry", True) from None
    today = today_micro(conn)
    if estimate <= 0:
        return BudgetDecision(True, None, cap, today, 0)
    return check_budget(today, estimate, cap)


def next_paid_step(job: dict) -> str | None:
    for s in sorted(job["steps"], key=lambda s: s["ordinal"]):
        if s["state"] in ("pending", "running") and s["name"] in PAID_STEPS:
            return s["name"]
    return None


def pause_reason(step: str, decision: BudgetDecision) -> str:
    return f"Paused before {step}: {decision.reason}."
