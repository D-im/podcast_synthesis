---
title: 'Story 1.5: See what every run costs'
type: 'feature'
created: '2026-09-30'
status: 'done'
baseline_commit: 'e6fc3917474f48c9ec9a08e22e189607069bc134'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-1-context.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Nothing records what a run costs. Real paid calls start in Story 1.6, and the Daily Cap in Epic 3 can only be trusted if every cent is already in one ledger.

**Approach:** Add an append-only spend ledger and a `Meter` that adapters must call to record each paid call. The pipeline hands every adapter a meter bound to the current Episode and step. The Episode status fragment shows total and per-step spend, and every page shows today's spend (local calendar day) against the Daily Cap from config. Fakes can be given a cost so all of this is testable without spending.

## Boundaries & Constraints

**Always:**
- Ledger table `spend_ledger(id, video_id FK, step, provider, amount_micro_usd INTEGER, created_at)` via migration 3. Money is integer micro-dollars. `created_at` is UTC in one fixed format (`%Y-%m-%dT%H:%M:%S.%fZ`) so text comparison orders correctly.
- Append-only: SQLite triggers abort any UPDATE or DELETE on the ledger. Only `app/store/spend.py` writes to it, and only `app/core/meter.py` calls its append function.
- Ports gain a `Meter` Protocol (`record(provider, amount_micro_usd)`). Each port method takes `meter` as a new argument. The pipeline creates a step-bound meter for each step and passes it in.
- `record` rejects anything that is not a non-negative `int` (no floats, no bools) with `ValueError`, writing nothing. A `to_micro(usd)` helper converts dollars to micro-dollars with round-half-up.
- A cost recorded before a step fails stays in the ledger.
- Fakes use `FakeBehavior.cost_micro`: when greater than 0, each fake call records it through the meter as provider `fake`. At 0 they record nothing.
- "Today" is the local calendar day. Its bounds are local midnight to next local midnight, converted to UTC (DST-correct, built from a naive local date then `.astimezone()`).
- Display: Episode spend as `$x.xxxx` (4 decimals, rounded); the header as `Today: $x.xx of $y.yy`. The header appears on every HTML page through a shared base template. The Daily Cap comes from `config.daily_cap_usd` passed to `create_app`. No enforcement in this story.
- Web only reads the ledger.

**Never:**
- No cap enforcement, estimates, pausing, real adapters or vendor pricing (later stories). No floats stored for money. No UPDATE or DELETE path for the ledger.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Charge recorded | Fake cost 250000 at `transcribe` | One ledger row: episode, `transcribe`, `fake`, 250000, UTC timestamp | N/A |
| Zero cost | `cost_micro = 0` | No rows | N/A |
| Episode breakdown | Costs on two steps | Fragment shows each step's amount and an exact total | N/A |
| No spend | New Episode | Fragment shows `$0.0000` total | N/A |
| Today's total | Rows at 23:30 yesterday, 00:30 and 23:30 today, 00:30 tomorrow (local) | Only today's two counted | N/A |
| Header everywhere | Home, Episode page | `Today: $x.xx of $5.00` shown | N/A |
| Append-only | `UPDATE` or `DELETE` on ledger | Aborted by trigger; rows unchanged | IntegrityError or similar |
| Bad amount | `-1`, `1.5`, `True` | `ValueError`, nothing written | N/A |
| Charge then fail | Fake charges then raises | Step `failed`; ledger row remains | N/A |
| Single writer | Source scan | Ledger INSERT only in `app/store/spend.py`; append imported only by `app/core/meter.py` | Test fails otherwise |
| Upgrade | Populated v2 database | Migration 3 adds the table and triggers; data intact | N/A |

</frozen-after-approval>

## Code Map

- `app/ports/__init__.py` -- the four Protocols and `Adapters`; add `Meter` Protocol and the `meter` argument to each method.
- `app/adapters/fakes.py` -- `FakeBehavior.cost_micro` already exists, unused. Fakes need the new argument.
- `app/core/pipeline.py` -- `run_step` calls each adapter; build the step meter there. `sanitize` and skip logic stay as they are.
- `app/store/migrations.py` -- list ends at migration 2; add 3. `app/store/episodes.py` shows the store style (plain dicts).
- `app/web/app.py`, `templates/home.html`, `episode.html`, `_status.html`, `app/web/status.py` -- add a base template with the header; add spend to the fragment and view model. `create_app(warnings, data_dir)` gains `daily_cap_usd`; `app/main.py` passes `config.daily_cap_usd`.
- `tests/test_pipeline.py` calls adapter methods directly in places and needs the new argument.

## Tasks & Acceptance

**Execution:**
- [ ] `app/store/migrations.py` -- migration 3: `spend_ledger` plus no-UPDATE and no-DELETE triggers -- ledger
- [ ] `app/store/spend.py` -- `append`, `total_for_episode`, `by_step`, `total_between(start_utc, end_utc)`, fixed-format UTC stamp helper -- all ledger SQL
- [ ] `app/core/meter.py` -- `StepMeter(conn, video_id, step)`, validation, `to_micro`, `local_day_bounds_utc(now)`, `format_usd` helpers -- single recording path
- [ ] `app/ports/__init__.py`, `app/adapters/fakes.py`, `app/core/pipeline.py` -- `Meter` port, `meter` argument on all methods, fakes record `cost_micro`, pipeline passes a step-bound meter -- wiring
- [ ] `app/web/` -- shared `base.html` with the header, spend in `_status.html` and `status.py`, `daily_cap_usd` into `create_app`; `app/main.py` passes it -- display
- [ ] `tests/` -- every matrix row, including a timezone test (`TZ` set with `time.tzset`, restored afterwards) and a populated-v2 upgrade test; update existing tests for the new signature and markup -- verification

**Acceptance Criteria:**
- Given a fake adapter with a cost, when a step runs, then exactly one ledger row exists for that episode and step and the Episode page shows it.
- Given rows across a local day boundary, when the header is computed, then only the local day's rows are counted.
- Given the ledger, when any UPDATE or DELETE is attempted, then it fails and the rows are unchanged.
- Given the source tree, when scanned, then the ledger is written only from `app/store/spend.py` and appended to only through `app/core/meter.py`.
- Given a failed step after a charge, when the Episode page is viewed, then the spend is still shown.

## Implementation Notes

- Built as specified: migration 3 (`spend_ledger`, two indexes, append-only triggers), `app/store/spend.py`, `app/core/meter.py` (`StepMeter`, `to_micro`, `format_usd`, `local_day_bounds_utc`), `Meter` port and `meter` argument on all four ports, fakes recording `cost_micro` as provider `fake`, `base.html` header, spend in the status fragment, `daily_cap_usd` passed from `config` into `create_app`.
- Choices: if the header's ledger read fails it shows `Today: unavailable (cap $x.xx)` instead of a 500; `create_app` defaults the cap to 5.0 for older call sites; per-step amounts show only for steps that have spend.
- Review patches: the meter rejects an empty or non-string provider (the ledger cannot be corrected later); header fallback wording fixed and tested; `main.run()` test with a 7.5 cap proves the configured cap reaches the page; `Meter` docstring says to record as soon as a call is billed, before parsing the response.
- Verified live: header shows `Today: $0.00 of $5.00` on home and episode pages, status shows `Spend: $0.0000`, schema is version 3 with both triggers.

## Spec Change Log

## Review Triage Log

| Finding | Verdict | Route | Evidence |
|---|---|---|---|
| Configured cap wiring into the header untested (test and default both 5.0) | medium | patch | Test with a 7.5 cap added |
| Header fallback untested; wording awkward | medium | patch | Test and new wording `unavailable (cap $x.xx)` |
| Meter accepts empty or non-string provider; ledger cannot be corrected | medium | patch | Validation and tests added |
| Meter protocol silent on when to record | low | patch | Docstring added |
| Retried steps sum across Jobs in the per-step display | low | rejected | Retry cost is real spend; episode-level total is the intent |
| Cap displayed but not enforced | low | rejected | Epic 3 (Stories 3.3, 3.4) |
| Huge or negative cap to `to_micro` | low | rejected | Config validation already rejects negative, NaN, inf |
| DST zones with a missing midnight | low | rejected | Edge case unlikely for this user; fix adds complexity |
| Single-writer scan bypassable by aliasing | low | rejected | Convention guard, not a security boundary |
| 404 JSON page has no header; `except` only catches expected errors | low | rejected | Acceptable |
| Fakes charge on every step; zero-cost step hidden; web imports core helpers; style and shadowing notes | low | rejected | No named harm |

## Verification

**Commands:**
- `uv run pytest -q` -- expected: all tests pass
- `uv run podcast-synthesis`, submit a link -- expected: header shows `Today: $0.00 of $5.00`; Episode status shows `$0.0000`
- `sqlite3 data/podcast_synthesis.db "PRAGMA user_version; .schema spend_ledger"` -- expected: version 3, table and two triggers
