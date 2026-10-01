---
title: 'Story 3.4: Pause a Job that hits the cap mid-run, and resume it on request'
type: 'feature'
created: '2026-10-01'
status: 'done'
baseline_commit: '06e51f5'
route: 'dispatch'
review_loop_iteration: 0
---

## Intent

Before each paid step (transcribe, summarize, verify) the pipeline asks one function, `guard.check_step`, whether today's actual ledger spend plus that step's estimate fits the Daily Cap (`budget.check_budget`, the same decision used by Story 3.3). If not, the Job moves to `paused` at that step boundary with every artifact kept, and the Episode page says it is paused because of the Daily Cap with the reason. Nothing resumes by itself. A Resume button appears once the budget allows the next step; Resume queues the same Job, which skips finished steps and never repeats paid work.

## Decisions (built without a separate approval round, at the user's request to keep building)

- `app/core/guard.py` is the only place that makes the decision: per-step estimates come from the stored Episode duration (transcribe) and the stored Transcript and One-Pager (summarize, verify) through the existing estimator. A step estimated at $0 (free or fake provider, nothing to size) is always allowed. An unreadable config fails the step (retryable, nothing spent) instead of guessing.
- `pipeline.run_job(..., config_path)` calls the guard before each paid step and returns `paused`; `Worker(config_path=...)` passes it, `main.py` passes the real config. A Worker built without a config path (tests) does not check. A paused Job is not picked up by the worker, so it never blocks other Jobs; a queued Job that hits the cap at its first paid step also pauses. Spend is read from the ledger at each check, so an overrun counts.
- Migration 11: `jobs.pause_reason`. `episodes.pause_job`, `episodes.resume_paused_job` (atomic, only for a paused latest Job).
- `POST /episodes/{id}/resume` re-checks the budget; refused with a 409 message while over the cap. The Resume button and the "cannot resume yet" text are decided at page load. The library label reads "Paused (Daily Cap)". Retry (3.2) and recovery (3.1) are unchanged: a retried paid step goes through the same guard, and recovery leaves paused Jobs alone.

## Verification

- `uv run pytest -q` passes offline, including a fake-adapter Job that crosses the cap, pauses before verify with artifacts kept, and resumes to run only verify. Not done: no real-money run and no code-review pass (story is in `review`).
