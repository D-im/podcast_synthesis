"""Story 2.3: loader, scoring helper and report for the planted-error fixtures.

The fixtures live in tests/fixtures/planted/: a fictional Transcript, a One-Pager built from it
with planted false claims and one omitted idea, and an answer key. `evaluate` compares a real (or
scripted) VerificationResult with the key.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from app.ports import OnePager, Transcript, VerificationResult

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "planted"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def load_fixtures() -> tuple[Transcript, OnePager, dict]:
    """The Transcript, the One-Pager and the answer key."""
    return (Transcript.from_dict(_load("transcript.json")),
            OnePager.from_dict(_load("one_pager.json")),
            _load("answer_key.json"))


def _hits(text: str, markers: list[str]) -> bool:
    """A marker counts only as a whole token, so "41" does not match "2041" or "1.41"."""
    low = text.lower()
    return any(re.search(r"(?<![\w.])" + re.escape(m.lower()) + r"(?!\w)", low) for m in markers)


def evaluate(result: VerificationResult, key: dict) -> dict:
    """Score a checker result against the key.

    Each key claim is matched to a checker claim whose text holds one of its markers (case
    insensitive, whole tokens). A checker claim is credited to one key claim only: if a reworded
    or merged claim holds markers of two key claims, the first key claim takes it and the other
    shows as not listed. A planted false claim is caught only when its matched claim is marked
    unsupported; a false claim the checker never listed counts as missed (and as `unlisted`).
    """
    checker = list(result.claims)
    used: set[int] = set()
    rows = []
    for kc in key["claims"]:
        cands = [i for i, c in enumerate(checker) if _hits(c.claim, kc["markers"])]
        free = [i for i in cands if i not in used]
        pick = free[0] if free else None
        if pick is not None:
            used.add(pick)
        matched = checker[pick] if pick is not None else None
        rows.append({
            "id": kc["id"], "truth": kc["truth"], "kind": kc.get("kind"), "text": kc["text"],
            "matched": matched.claim if matched else None,
            "unsupported": (matched.verdict == "unsupported") if matched else None,
            "note": matched.note if matched else "",
        })
    all_hit = {i for i, c in enumerate(checker)
               if any(_hits(c.claim, kc["markers"]) for kc in key["claims"])}
    false_rows = [r for r in rows if r["truth"] == "false"]
    caught = [r["id"] for r in false_rows if r["unsupported"] is True]
    missed = [r["id"] for r in false_rows if r["unsupported"] is not True]
    wrongly = [r["id"] for r in rows if r["truth"] == "true" and r["unsupported"] is True]
    unlisted = [r["id"] for r in rows if r["matched"] is None]
    extra = [c.claim for i, c in enumerate(checker) if i not in all_hit]
    omissions = []
    for om in key.get("omitted_ideas", []):
        hit = next((i for i in result.ideas if _hits(f"{i.title} {i.description}", om["markers"])), None)
        omissions.append({"title": om["title"], "found": hit is not None,
                          "idea": hit.title if hit else None,
                          "coverage": hit.coverage if hit else None})
    return {
        "rows": rows, "caught": caught, "missed": missed, "wrongly_flagged": wrongly,
        "unlisted": unlisted, "extra_checker_claims": extra, "omissions": omissions,
        "planted": len(false_rows), "true_claims": len(rows) - len(false_rows),
        "accuracy": result.accuracy, "coverage": result.coverage,
        "checker_claims": len(checker),
    }


def cost_dollars(meter_rows) -> float:
    """Dollars from metered rows of (provider, micro-dollars, ref)."""
    return sum((r[1] or 0) for r in meter_rows) / 1_000_000


def format_report(ev: dict, meter_rows=None, title: str = "Planted-error check") -> str:
    lines = [title, "=" * len(title)]
    lines.append(f"Planted false claims caught: {len(ev['caught'])} of {ev['planted']}")
    lines.append("  caught: " + (", ".join(ev["caught"]) or "none"))
    lines.append("  missed: " + (", ".join(ev["missed"]) or "none"))
    lines.append(f"True claims wrongly flagged: {len(ev['wrongly_flagged'])} of {ev['true_claims']}"
                 + (" (" + ", ".join(ev["wrongly_flagged"]) + ")" if ev["wrongly_flagged"] else ""))
    lines.append("Key claims the checker never listed: " + (", ".join(ev["unlisted"]) or "none"))
    lines.append(f"Checker claims matching no key claim: {len(ev['extra_checker_claims'])}")
    for t in ev["extra_checker_claims"]:
        lines.append(f"  - {t}")
    for om in ev["omissions"]:
        if om["found"]:
            lines.append(f"Omitted idea '{om['title']}': found as '{om['idea']}', coverage {om['coverage']}")
        else:
            lines.append(f"Omitted idea '{om['title']}': not found in the checker's idea list")
    acc = ev["accuracy"]
    cov = ev["coverage"]
    lines.append("Fidelity: accuracy " + (f"{acc:.1%}" if acc is not None else "n/a")
                 + (f", coverage {cov:.1%}" if cov is not None else ""))
    if meter_rows is not None:
        lines.append(f"Cost: ${cost_dollars(meter_rows):.4f} ({len(meter_rows)} paid calls)")
    lines.append("Per claim:")
    for r in ev["rows"]:
        state = ("not listed" if r["matched"] is None
                 else "unsupported" if r["unsupported"] else "supported")
        label = f"false/{r['kind']}" if r["truth"] == "false" else "true"
        lines.append(f"  {r['id']} [{label}] -> {state}")
    return "\n".join(lines)
