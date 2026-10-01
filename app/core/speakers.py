"""Speaker names (Story 4.1): per-Episode names for the Transcript's speaker labels.

The stored Transcript file is never changed; names are applied to a copy.
"""
from __future__ import annotations

import unicodedata
from dataclasses import replace

from app.ports import Transcript

MAX_NAME = 60


def labels(transcript: Transcript) -> list[str]:
    """Distinct non-empty speaker labels, in order of first appearance."""
    seen: list[str] = []
    for s in transcript.segments:
        label = (s.speaker or "").strip()
        if label and label not in seen:
            seen.append(label)
    return seen


def validate(raw: dict[str, str], known: list[str]) -> dict[str, str] | str:
    """Clean mapping of label to name (empty names dropped), or a message."""
    clean: dict[str, str] = {}
    for label, value in raw.items():
        if label not in known:
            return "That speaker is not in this Transcript. Nothing was saved."
        name = (value or "").strip()
        if not name:
            continue
        if len(name) > MAX_NAME:
            return f"A name is too long (at most {MAX_NAME} characters). Nothing was saved."
        if any(unicodedata.category(c).startswith("C") for c in name):
            return "Names cannot contain line breaks or control characters. Nothing was saved."
        clean[label] = name
    return clean


def apply(transcript: Transcript, names: dict[str, str] | None) -> Transcript:
    """A new Transcript with each named label replaced by its name."""
    if not names:
        return transcript
    return Transcript(tuple(
        replace(s, speaker=names.get((s.speaker or "").strip(), s.speaker))
        for s in transcript.segments))
