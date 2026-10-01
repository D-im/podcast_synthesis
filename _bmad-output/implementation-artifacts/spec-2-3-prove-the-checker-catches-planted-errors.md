---
title: 'Story 2.3: Prove the checker catches planted errors'
type: 'chore'
created: '2026-09-30'
status: 'done'
baseline_commit: '7803949dbffd3946be98a70cb0e1a0b5b3043488'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-1-context.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The Fidelity Score is only worth trusting if the checker is known to catch real errors. So far it has been run on one real One-Pager with no answer key, and on a two-sentence smoke test.

**Approach:** Commit a small fictional Transcript and a One-Pager built from it with eight planted false claims of different kinds and one major idea left out, plus an answer key that says exactly what is true, false and omitted. Free offline tests prove the fixtures are consistent and that the scoring helper reads results correctly. A separate paid test, clearly labelled and double-gated, runs the fixtures through the real checker and reports which planted errors it caught, which it missed, how many true claims it wrongly flagged, whether it found the omitted idea, and what the run cost. Diimrem writes down the findings as the basis for how far to trust the score.

## Boundaries & Constraints

**Always:**
- Fixtures live in `tests/fixtures/planted/`: `transcript.json` (the app's Transcript format with speakers, about 30 segments, at least 3 words per quoted fact, a made-up setting so outside knowledge cannot help), `one_pager.json` (the app's OnePager format, five sections like the default prompt), and `answer_key.json`.
- The fictional episode: Port Alder ferry authority, guest Dana Okafor (chief engineer, speaker B) and a host (speaker A). Facts in the Transcript: the 2019 board decision to postpone dredging to save about four million dollars; channel depth falling from eleven meters to 9.1 meters at low tide; fourteen groundings in 2023; Okafor recommended the deferral and regrets it; repairs and refunds of about 6.2 million dollars in 2023; two battery-electric ferries ordered in 2022 from a yard in Gdansk at thirty-eight million dollars each; diesel fuel costing about 2.9 million dollars a year and a seventy percent cut expected; a 22 megawatt shore-power upgrade costing twenty-seven million dollars with Northline Power paying forty percent; battery capacity dropping eighteen percent below minus five Celsius and a third charging stop at Fenwick Point to compensate; about 1.8 million riders a year with a seven percent fall last year, most of it caused by delays; advice to price deferred maintenance honestly and test batteries in the worst season first; and the fishermen's co-op compromise (pile driving only from October to March, outside the April to September herring season, in return for staging access to the co-op's pier).
- The One-Pager omits the fishermen's co-op compromise entirely (the planted omission) and contains exactly 14 true claims and 8 planted false ones, of these kinds: wrong number (groundings stated as 41; diesel cost stated as 9.2 million), wrong speaker (the host said to have recommended the deferral), fabricated fact (a city council vote of 5 to 2), reversed meaning (she would make the 2019 decision again), exaggeration (delays caused all of the ridership fall), invented quote (a "Notable quotes" line she never said), and wrong entity (Southline Power instead of Northline Power). Two of the true claims are verbatim quotes from the Transcript.
- `answer_key.json`: for every claim `id`, `section`, `text` (as it appears in the One-Pager), `truth` (`true` or `false`), `kind` for false ones, `markers` (lowercase substrings, any one of which identifies the claim in a checker's reworded claim text, chosen so no two key claims share a marker), and for true claims an `evidence` quote copied exactly from one Transcript segment. It also lists `omitted_ideas` (title and markers) and states the counts.
- A helper `tests/planted.py` loads the fixtures and has `evaluate(result, key)` that returns: for each key claim, which checker claim matched (by marker, case-insensitive) and whether it was marked unsupported; planted false claims caught and missed; true claims wrongly flagged; key claims the checker never listed; checker claims matching no key claim; for the omitted ideas, whether the checker's idea list contained one (by marker in title or description) and what coverage it got. It also formats a plain text report including cost in dollars from the metered rows.
- Offline tests (free, no network): the fixtures parse and load into the app's types; counts match the key (14 true, 8 false, 22 total); every true claim's evidence is accepted by the real `QuoteIndex`; every false claim's text appears in the One-Pager and differs from the Transcript in the stated way (the key's false claims have no accepted quote); every marker appears in its claim text and no marker is shared; no `omitted_ideas` marker appears in the One-Pager text; every true fact is present in the Transcript; `evaluate` run against a scripted perfect checker built from the key reports all 8 caught, 0 wrongly flagged, the omission found and missing; and against a scripted weak checker that marks everything supported it reports 0 caught and the right missed list.
- The paid test, `tests/test_planted_errors.py::test_real_checker_on_planted_errors`, runs only when both `RUN_NETWORK_TESTS=1` and `RUN_PAID_CHECKER_TEST=1` and `OPENAI_API_KEY` are set; its name and docstring say it spends money (expected under 15 cents per run). `PLANTED_RUNS` (default 1, maximum 5) repeats the check because the model is not deterministic. It prints the report (use `-s`), and when `PLANTED_REPORT` names a file it also writes the report there. It asserts only that each run produced a result and a cost under 50 cents; it never fails on the checker's quality, since the quality is the finding.
- Fixture design rule: no real person, company or place named in a way that makes the facts checkable from memory, except the shipyard city Gdansk used as plain scenery.
- After building, run the paid test with `PLANTED_RUNS=3` once and save the report to `_bmad-output/implementation-artifacts/checker-trust-2-3.md`, with a short findings section: what the checker caught and missed, wrongly flagged true claims, whether it found the omitted idea in each run, run-to-run variation, cost, and a plain statement of how far the Fidelity Score can be trusted and for what.

**Never:**
- No changes to the app, adapters, prompts or thresholds in this story. No fixture text from real transcripts. Do not make the paid test run by default or in the offline suite. Do not tune the fixtures after seeing the checker's results to make it look better.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Fixtures load | The three JSON files | Parse into the app's types and the key; 22 claims, 8 false | Test fails on a mismatch |
| Evidence real | Every true claim's quote | Accepted by `QuoteIndex` | N/A |
| False claims are false | Each planted false claim | Not supported by any accepted quote; differs as stated | N/A |
| Markers | All markers | Present in their claim text, none shared | N/A |
| Omission | `omitted_ideas` markers | Absent from the One-Pager | N/A |
| Perfect checker | Scripted from the key | 8 caught, 0 wrongly flagged, omission found and missing | N/A |
| Rubber stamp | Scripted to support everything | 0 caught, 8 missed listed, 0 false alarms | N/A |
| Silent checker | Scripted checker that lists fewer claims | Unlisted key claims reported separately, planted ones counted as missed | N/A |
| Reworded claim | Checker text differs in wording but keeps a marker | Matched to the key claim | N/A |
| Paid test off | No gate variable set | Skipped | N/A |
| Paid test on | All gates and a key | Report printed with caught, missed, false alarms, omission, cost | Cost over 50 cents fails the test |

</frozen-after-approval>

## Code Map

- `app/adapters/openai.py` -- `OpenAIVerifier` (`from_config`, `verify`), `QuoteIndex`; the real checker the paid test uses. `app/ports/__init__.py` -- `Transcript`, `OnePager`, `VerificationResult` (`claims`, `ideas`, `missed_ideas`, `accuracy`, `coverage`), `Claim`, `IdeaCheck`.
- `tests/test_openai.py` and `tests/test_coverage.py` -- `FakeClient`, `claim`, `ideas_reply`, `coverage_reply`, `cov_entry`, `Meter`, `reply`, `make`: reuse these to build scripted checkers. The smoke tests there are the pattern for network gating (`@pytest.mark.network`, `RUN_NETWORK_TESTS`).
- `tests/conftest.py` -- blocks sockets unless a test is marked `network`. `config.toml` / `load_config` -- model and pricing for the real checker.
- New: `tests/fixtures/planted/` (three files), `tests/planted.py`, `tests/test_planted_errors.py`, and the findings file under `_bmad-output/implementation-artifacts/`.

## Tasks & Acceptance

**Execution:**
- [ ] `tests/fixtures/planted/transcript.json`, `one_pager.json`, `answer_key.json` -- the fictional episode, the planted One-Pager and the key -- fixtures
- [ ] `tests/planted.py` -- loader, `evaluate`, report formatter -- helper
- [ ] `tests/test_planted_errors.py` -- the offline fixture and evaluator tests, the double-gated paid test with `PLANTED_RUNS` and `PLANTED_REPORT` -- verification
- [ ] Run the paid test once with `PLANTED_RUNS=3` and write `checker-trust-2-3.md` -- the findings

**Acceptance Criteria:**
- Given the fixtures, when the offline tests run, then they confirm 14 true and 8 planted false claims, accepted evidence for every true claim, no accepted evidence for any false one, and no omitted-idea marker in the One-Pager.
- Given a scripted perfect checker, when evaluated, then the helper reports all planted errors caught and the omission found; given a rubber-stamp checker it reports none caught.
- Given the paid test without its gate variables, when the suite runs, then it is skipped and no network is used.
- Given the paid test with its gates, when it runs, then it prints which planted errors were caught and missed, how many true claims were wrongly flagged, whether the omitted idea was found, and the cost.
- Given the paid run, when it finishes, then the findings file records the results and a plain statement of how far the score can be trusted.

## Implementation Notes

- Built as specified: `tests/fixtures/planted/` (31-segment fictional transcript, planted One-Pager with 14 true and 8 false claims, answer key with markers and evidence quotes), `tests/planted.py` (`evaluate`, `format_report`), `tests/test_planted_errors.py` (offline fixture and evaluator tests, double-gated paid test). No app code, adapters, prompts or thresholds changed.
- Result (details in `checker-trust-2-3.md`): over six paid runs the real checker caught 8 of 8 planted false claims every time, wrongly flagged 0 of 14 true claims, never failed to list a key claim, and found the omitted idea and rated it missing every time; about $0.04 per run. This is the easy case (short, clean, invented), stated plainly in the findings; accuracy and coverage numbers move between runs and are not comparable.
- The fixtures were not tuned after seeing results. The harness matching was tightened on review (below) and the paid test re-run once with the final code to confirm it still matches real output.
- Review patches (test harness only): markers match whole tokens ("41" no longer matches "2041" or "1.41"); a checker claim is credited to one key claim only, so merged claims cannot be double counted; the report formatter survives missing numbers and unmetered rows; the paid test prints and writes each run's report before asserting (so a later failure cannot lose paid results), stops if spending passes 50 cents, checks the ideas and coverage stages ran, and rejects an invalid `PLANTED_RUNS`; the kinds of planted error are asserted exactly; tests added for each.

## Spec Change Log

## Review Triage Log

| Finding | Verdict | Route | Evidence |
|---|---|---|---|
| Markers match as raw substrings ("41" in "2041") | medium | patch | Whole-token matching; test |
| One checker claim credited to several key claims | medium | patch | One claim per key claim; merged-claim test |
| Paid test loses earlier reports if a later run fails | medium | patch | Print and write inside the loop |
| Paid test does not check ideas and coverage stages ran; no cumulative cost stop; `PLANTED_RUNS` silently clamped | low | patch | Asserted, 50 cent stop, validation |
| `format_report` crashes on missing accuracy; cost helper on a None amount | low | patch | Guarded; test |
| Kinds of false claim asserted as a set; loose segment count | low | patch | Exact lists |
| Markers brittle against paraphrase | low | rejected | Whole-token markers chosen to be short; unmatched claims are reported as not listed, and none were in six real runs |
| Fixture has one omission and no inference-type error; only the easy case | low | rejected | Stated limits in the findings; fixtures deliberately not tuned afterwards |
| Verification gap reviewer | none found | none | No production code changed |

## Verification

**Commands:**
- `uv run pytest -q` -- expected: all tests pass offline, the paid test skipped
- `RUN_NETWORK_TESTS=1 RUN_PAID_CHECKER_TEST=1 PLANTED_RUNS=3 PLANTED_REPORT=/tmp/planted.txt uv run pytest -q -s tests/test_planted_errors.py -k real_checker` -- expected: a report for 3 runs; total under 50 cents
