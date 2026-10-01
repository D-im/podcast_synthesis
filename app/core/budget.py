"""The one budget decision: today's actual spend plus an estimate against the Daily Cap (AD-8).

Pure. Story 3.4 calls the same function before each paid step.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.core.meter import format_usd


@dataclass(frozen=True)
class BudgetDecision:
    allowed: bool
    reason: str | None
    cap_micro: int
    today_micro: int
    estimate_micro: int


def check_budget(today_micro: int, estimate_micro: int, cap_micro: int) -> BudgetDecision:
    """Allowed only while today's spend is below the cap and spend plus the estimate fits in it."""
    reason = None
    if today_micro >= cap_micro:
        reason = (f"today's spend ({format_usd(today_micro, 2)}) has already reached the "
                  f"Daily Cap ({format_usd(cap_micro, 2)})")
    elif today_micro + estimate_micro > cap_micro:
        reason = (f"today's spend ({format_usd(today_micro, 2)}) plus the estimate "
                  f"({format_usd(estimate_micro)}) would go over the Daily Cap "
                  f"({format_usd(cap_micro, 2)})")
    return BudgetDecision(reason is None, reason, cap_micro, today_micro, estimate_micro)
