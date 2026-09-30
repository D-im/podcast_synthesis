"""The single recording path for paid calls, plus money and local-day helpers."""
from __future__ import annotations

import sqlite3
from datetime import datetime, time, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

from app.store import spend

MICRO = 1_000_000


def to_micro(usd: int | float | Decimal | str) -> int:
    """Dollars to integer micro-dollars, rounding half up."""
    if isinstance(usd, bool):
        raise ValueError("amount must be a number")
    try:
        value = Decimal(str(usd)) if isinstance(usd, float) else Decimal(usd)
    except Exception:
        raise ValueError("amount must be a number") from None
    if not value.is_finite():
        raise ValueError("amount must be finite")
    return int((value * MICRO).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _format(micro: int, decimals: int) -> str:
    unit = 10 ** (6 - decimals)
    scaled = (abs(micro) + unit // 2) // unit  # half up on the magnitude
    sign = "-" if micro < 0 and scaled else ""
    whole, frac = divmod(scaled, 10 ** decimals)
    return f"{sign}${whole}.{frac:0{decimals}d}"


def format_usd(micro: int, decimals: int = 4) -> str:
    """Micro-dollars as $x.xxxx (default) or $x.xx, rounded half up."""
    if decimals not in (2, 4):
        raise ValueError("decimals must be 2 or 4")
    return _format(micro, decimals)


def local_day_bounds_utc(now: datetime | None = None) -> tuple[datetime, datetime]:
    """[local midnight, next local midnight) as UTC datetimes. DST-correct."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    today = now.astimezone().date()
    start = datetime.combine(today, time.min).astimezone()  # naive local -> aware local
    end = datetime.combine(today + timedelta(days=1), time.min).astimezone()
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


class StepMeter:
    """A Meter bound to one Episode and step; every paid call is recorded through it."""

    def __init__(self, conn: sqlite3.Connection, video_id: str, step: str):
        self.conn = conn
        self.video_id = video_id
        self.step = step

    def record(self, provider: str, amount_micro_usd: int, ref: str | None = None) -> None:
        if isinstance(amount_micro_usd, bool) or not isinstance(amount_micro_usd, int):
            raise ValueError("amount_micro_usd must be an int")
        if amount_micro_usd < 0:
            raise ValueError("amount_micro_usd must not be negative")
        if not isinstance(provider, str) or not provider.strip():
            raise ValueError("provider must be a non-empty string")
        if ref is not None and (not isinstance(ref, str) or not ref.strip()):
            raise ValueError("ref must be a non-empty string")
        spend.append(self.conn, self.video_id, self.step, provider, amount_micro_usd, ref=ref)
