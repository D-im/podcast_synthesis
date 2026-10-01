"""Story 2.3: prove the checker catches planted errors.

Offline tests check the fixtures and the scoring helper (free). The one paid test at the bottom
runs the fixtures through the real checker and spends money; it is double-gated.
"""
import os

import pytest

from app.adapters import openai as oa
from app.adapters.openai import QuoteIndex, normalize
from app.config import load_config
from app.ports import VerificationResult
from tests import planted
from tests.planted import evaluate, format_report, load_fixtures
from tests.test_openai import (
    FakeClient, Meter, claim, coverage_reply, cov_entry, ideas_reply, make, reply,
)

TRANSCRIPT, PAGE, KEY = load_fixtures()
CLAIMS = KEY["claims"]
TRUE = [c for c in CLAIMS if c["truth"] == "true"]
FALSE = [c for c in CLAIMS if c["truth"] == "false"]
INDEX = QuoteIndex(TRANSCRIPT)
PAGE_TEXT = oa.one_pager_text(PAGE)
OMITTED = KEY["omitted_ideas"][0]


def padded(text):
    return f" {normalize(text)} "


def run_checker(claims, ideas=None, coverage=None):
    ideas = ideas or [dict(id=1, title="The dredging deferral", description="Postponed dredging."),
                      dict(id=2, title=OMITTED["title"],
                           description="The fishermen's co-op agreed to pile driving limits.")]
    coverage = coverage or [cov_entry(1), cov_entry(2, "missing", "", "absent")]
    client = FakeClient(reply(claims), ideas=ideas_reply(ideas),
                        coverage=coverage_reply(coverage))
    m = Meter()
    return make(client).verify(TRANSCRIPT, PAGE, m), m


def perfect_claims():
    return [claim("supported", c["evidence"], c["section"], c["text"], "ok") if c["truth"] == "true"
            else claim("unsupported", "", c["section"], c["text"], "not in transcript")
            for c in CLAIMS]


def rubber_stamp_claims():
    ev = TRANSCRIPT.segments[3].text
    return [claim("supported", ev, c["section"], c["text"], "ok") for c in CLAIMS]


# --- fixtures ---

def test_fixtures_load_into_app_types():
    assert len(TRANSCRIPT.segments) == 31
    assert {s.speaker for s in TRANSCRIPT.segments} == {"Speaker A", "Speaker B"}
    assert [s.name for s in PAGE.sections] == [
        "Summary", "Big ideas", "Actionable items", "Notable quotes", "Worth your time"]


def test_counts_match_the_key():
    assert (len(TRUE), len(FALSE), len(CLAIMS)) == (14, 8, 22)
    assert KEY["counts"] == {"true": 14, "false": 8, "total": 22}
    assert len({c["id"] for c in CLAIMS}) == 22
    assert sorted(c["kind"] for c in FALSE) == sorted([
        "wrong number", "wrong speaker", "fabricated fact", "reversed meaning",
        "exaggeration", "invented quote", "wrong entity"] + ["wrong number"])


def test_every_true_claim_has_accepted_evidence_from_one_segment():
    for c in TRUE:
        ev = c["evidence"]
        assert len(ev.split()) >= 3, c["id"]
        assert INDEX.found(ev), c["id"]
        assert sum(padded(ev) in padded(s.text) for s in TRANSCRIPT.segments) == 1, c["id"]


def test_two_true_claims_are_verbatim_quotes():
    quotes = [c for c in TRUE if '"' in c["text"]]
    assert len(quotes) == 2
    for c in quotes:
        inner = c["text"].split('"')[1]
        assert INDEX.found(inner), c["id"]


def test_false_claims_have_no_key_evidence_and_are_not_in_the_transcript():
    text = padded(TRANSCRIPT.text)
    for c in FALSE:
        assert "evidence" not in c, c["id"]
        assert not INDEX.found(c["text"]), c["id"]
        assert not any(padded(m) in text for m in c["markers"]), c["id"]


def test_claim_text_appears_in_the_one_pager_under_its_section():
    sections = {s.name: s.text for s in PAGE.sections}
    for c in CLAIMS:
        assert c["text"] in sections[c["section"]], c["id"]


def test_markers_are_in_their_claim_and_not_shared():
    seen = {}
    for c in CLAIMS:
        assert c["markers"], c["id"]
        for m in c["markers"]:
            assert m == m.lower()
            assert m in c["text"].lower(), (c["id"], m)
            assert m not in seen, (m, seen.get(m), c["id"])
            seen[m] = c["id"]
            others = [o["id"] for o in CLAIMS if o is not c and m in o["text"].lower()]
            assert not others, (m, c["id"], others)


def test_omitted_idea_markers_are_absent_from_the_one_pager():
    assert OMITTED["title"] and OMITTED["markers"]
    for m in OMITTED["markers"]:
        assert m.lower() not in PAGE_TEXT.lower(), m
        assert m.lower() in TRANSCRIPT.text.lower(), m


def test_every_true_fact_is_in_the_transcript():
    facts = [
        "2019", "voted to postpone dredging", "four million dollars", "eleven meters to 9 1 meters",
        "fourteen groundings in 2023", "recommended the deferral",
        "6 2 million dollars", "two battery electric ferries", "gdansk", "thirty eight million",
        "2 9 million dollars a year", "seventy percent", "22 megawatt", "twenty seven million",
        "northline power", "forty percent", "eighteen percent below minus five celsius",
        "third charging stop at fenwick point", "1 8 million riders", "seven percent",
        "caused by delays", "pile driving only happens from october to march",
        "april to september herring season", "staging access", "price deferred maintenance honestly",
        "test batteries in the worst season first",
    ]
    text = padded(TRANSCRIPT.text)
    for f in facts:
        assert padded(f) in text, f
    assert "regret it" in TRANSCRIPT.text


# --- evaluate ---

def test_perfect_checker_catches_everything():
    r, m = run_checker(perfect_claims())
    ev = evaluate(r, KEY)
    assert len(ev["caught"]) == 8 and ev["missed"] == []
    assert ev["wrongly_flagged"] == [] and ev["unlisted"] == [] and ev["extra_checker_claims"] == []
    assert ev["omissions"][0]["found"] and ev["omissions"][0]["coverage"] == "missing"
    assert r.accuracy == round(14 / 22, 4)
    report = format_report(ev, m.rows)
    assert "caught: " in report and "Cost: $" in report and "found as" in report
    assert planted.cost_dollars(m.rows) == sum(x[1] for x in m.rows) / 1e6 > 0


def test_rubber_stamp_checker_catches_nothing():
    r, _ = run_checker(rubber_stamp_claims())
    ev = evaluate(r, KEY)
    assert ev["caught"] == [] and ev["missed"] == [c["id"] for c in FALSE]
    assert ev["wrongly_flagged"] == [] and ev["unlisted"] == []


def test_silent_checker_lists_fewer_claims():
    drop = {FALSE[0]["id"], FALSE[1]["id"], TRUE[0]["id"]}
    kept = [c for c in perfect_claims()
            if not any(c["claim"] == k["text"] and k["id"] in drop for k in CLAIMS)]
    r, _ = run_checker(kept)
    ev = evaluate(r, KEY)
    assert set(ev["unlisted"]) == drop
    assert FALSE[0]["id"] in ev["missed"] and FALSE[1]["id"] in ev["missed"]
    assert len(ev["caught"]) == 6 and ev["wrongly_flagged"] == []


def test_reworded_claim_still_matches_by_marker():
    f = FALSE[1]
    reworded = [claim("unsupported", "", "Summary", f"Per the page, {f['text'].upper()} (reworded)", "no")]
    r, _ = run_checker(reworded)
    ev = evaluate(r, KEY)
    assert ev["caught"] == [f["id"]]
    assert len(ev["unlisted"]) == 21


def test_wrongly_flagged_true_claims_and_extra_claims_and_missing_omission():
    t = TRUE[1]
    claims = [claim("unsupported", "", t["section"], t["text"], "x"),
              claim("supported", TRANSCRIPT.segments[3].text, "Summary", "Something else entirely", "ok")]
    r, _ = run_checker(claims, ideas=[dict(id=1, title="Unrelated", description="Nothing here.")],
                       coverage=[cov_entry(1)])
    ev = evaluate(r, KEY)
    assert ev["wrongly_flagged"] == [t["id"]]
    assert ev["extra_checker_claims"] == ["Something else entirely"]
    assert ev["omissions"][0]["found"] is False
    assert "not found" in format_report(ev)


def test_evaluate_works_on_a_bare_result():
    ev = evaluate(VerificationResult(accuracy=1.0), KEY)
    assert ev["caught"] == [] and len(ev["missed"]) == 8 and len(ev["unlisted"]) == 22


# --- paid test ---

def _runs():
    raw = os.environ.get("PLANTED_RUNS", "1")
    if not raw.isdigit() or not 1 <= int(raw) <= 5:
        raise ValueError(f"PLANTED_RUNS must be a whole number from 1 to 5, got {raw!r}")
    return int(raw)


@pytest.mark.network
@pytest.mark.skipif(
    os.environ.get("RUN_NETWORK_TESTS") != "1" or os.environ.get("RUN_PAID_CHECKER_TEST") != "1"
    or not os.environ.get(oa.KEY_VAR),
    reason="spends money: needs RUN_NETWORK_TESTS=1, RUN_PAID_CHECKER_TEST=1 and OPENAI_API_KEY")
def test_real_checker_on_planted_errors():
    """PAID TEST: spends real money on the OpenAI checker (expected under 15 cents per run).

    Runs the planted fixtures through the real checker PLANTED_RUNS times (default 1, max 5),
    prints the report (use -s) and writes it to PLANTED_REPORT if set. It never fails on the
    checker's quality, only if a run gives no result or costs 50 cents or more.
    """
    cfg = load_config()
    path = os.environ.get("PLANTED_REPORT")
    reports, spent = [], 0.0
    for n in range(1, _runs() + 1):
        assert spent < 0.50, f"stopping before run {n}: ${spent:.2f} already spent"
        meter = Meter()
        result = oa.OpenAIVerifier.from_config(cfg).verify(TRANSCRIPT, PAGE, meter)
        cost = planted.cost_dollars(meter.rows)
        spent += cost
        report = format_report(evaluate(result, KEY), meter.rows,
                               title=f"Planted-error check, run {n}")
        reports.append(report)
        print("\n" + report)                     # shown before any assertion can fail
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n\n".join(reports) + "\n")
        assert isinstance(result, VerificationResult) and result.claims
        assert result.ideas and result.coverage is not None   # the omission stage ran too
        assert cost < 0.50


# --- review patches: harness matching and reporting ---

def test_markers_match_whole_tokens_only():
    from tests.planted import _hits
    assert _hits("There were 41 groundings", ["41"]) and not _hits("in 2041 and 1.41 and 41k", ["41"])
    assert _hits("depth of 9.1 meters", ["9.1"]) and not _hits("depth of 19.1 meters", ["9.1"])
    assert _hits("The Host recommended it", ["the host"]) and not _hits("the hostile crowd", ["the host"])


def test_a_merged_checker_claim_is_credited_to_one_key_claim_only():
    from app.ports import Claim
    merged = Claim("Summary", "There were 41 groundings and diesel costs 9.2 million",
                   "unsupported", "", "n", False)
    ev = evaluate(VerificationResult(accuracy=0.0, claims=(merged,)), KEY)
    assert ev["caught"] == ["F02"] and "F03" in ev["unlisted"] and "F03" in ev["missed"]


def test_report_formatter_survives_missing_numbers():
    ev = evaluate(VerificationResult(accuracy=None), KEY)
    text = format_report(ev, [("openai", None, "r")])
    assert "accuracy n/a" in text and "$0.0000" in text


def test_planted_runs_rejects_bad_values(monkeypatch):
    from tests.test_planted_errors import _runs
    for bad in ("0", "6", "-1", "x", "", "2.5"):
        monkeypatch.setenv("PLANTED_RUNS", bad)
        with pytest.raises(ValueError):
            _runs()
    monkeypatch.setenv("PLANTED_RUNS", "3")
    assert _runs() == 3
