"""Advisory low-score flags, decided when a page is shown from the stored scores and the
current thresholds. Nothing here is stored, and a flag never hides or changes anything."""
from __future__ import annotations

import math
import tomllib
from dataclasses import dataclass
from pathlib import Path

from app.web.library import percent


@dataclass(frozen=True)
class Thresholds:
    accuracy: float = 0.9
    coverage: float = 0.75


def _valid(v) -> bool:
    return (not isinstance(v, bool) and isinstance(v, (int, float))
            and math.isfinite(v) and 0 <= v <= 1)


def read_thresholds(path: Path | None, last_good: Thresholds) -> Thresholds:
    """Thresholds from `[fidelity]` in the config file; any problem keeps `last_good`."""
    if path is None:
        return last_good
    try:
        fid = tomllib.loads(Path(path).read_text(encoding="utf-8"))["fidelity"]
        acc, cov = fid["accuracy_threshold"], fid["coverage_threshold"]
    except (OSError, ValueError, KeyError, TypeError):  # TOMLDecodeError and decode errors are ValueErrors
        return last_good
    if not (_valid(acc) and _valid(cov)):
        return last_good
    return Thresholds(float(acc), float(cov))


def flags(accuracy, coverage, t: Thresholds) -> list[str]:
    """Reasons, e.g. 'Low accuracy (80% is below 90%)'. A missing or invalid score is never
    flagged, and a score exactly at its threshold is not flagged."""
    out = []
    for name, score, limit in (("accuracy", accuracy, t.accuracy),
                               ("coverage", coverage, t.coverage)):
        if percent(score) is not None and score < limit:
            out.append(f"Low {name} ({percent(score)} is below {percent(limit)})")
    return out
