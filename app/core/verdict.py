"""Verdict validation (Story 2.5)."""
from __future__ import annotations

from dataclasses import dataclass

RATINGS = {"worth_it": "Worth it", "not_worth_it": "Not worth it"}
MAX_REASON = 5000


@dataclass(frozen=True)
class Clean:
    rating: str
    reason: str


@dataclass(frozen=True)
class Rejected:
    message: str


def validate(rating: str | None, reason: str | None) -> Clean | Rejected:
    if rating not in RATINGS:
        return Rejected("Choose worth it or not worth it.")
    text = (reason or "").strip()
    if not text:
        return Rejected("Give a reason for the Verdict.")
    if len(text) > MAX_REASON:
        return Rejected(f"The reason is too long (at most {MAX_REASON} characters).")
    return Clean(rating, text)


def label(rating: str) -> str:
    return RATINGS.get(rating, rating)
