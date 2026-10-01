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

### Review Findings (Epic 3 review of Stories 3.2, 3.3 and 3.4, diff 062e400..c2dc15c)

- [x] [Review][Patch] Production wiring of the Daily Cap (`Worker(config_path)`, `create_app(lookup)`) untested; removing either passed every test [tests/test_startup.py]
- [x] [Review][Patch] `getattr(adapters.downloader, "lookup", None)` silently turned submission admission off if a downloader lacked `lookup`; now required [app/main.py]
- [x] [Review][Patch] The guard could pause a transcribe whose AssemblyAI job is already saved (paid at the vendor) before collecting it; a saved vendor job ID now skips the guard [app/core/pipeline.py]
- [x] [Review][Patch] Regeneration refusal omitted cap, today's spend and estimate (Story 3.3 "refused in the same way") [app/web/app.py]
- [x] [Review][Patch] `ArithmeticError` (infinite duration) escaped admission as a 500; `today_micro` re-export hack removed [app/core/admission.py, app/web/app.py]
- [x] [Review][Defer] Cap is soft: only recorded spend counts, not estimates of other queued Jobs, so several admitted Jobs can together pass the cap — deferred: Story 3.3 defines the check as "actual spend plus the estimate"; a committed-spend model is a design change
- [x] [Review][Defer] Episodes created before Story 3.3 have no stored estimate, so no "estimate vs actual" line; transcribe is estimated from the duration the download step stores, so they are still checked — deferred: cosmetic

#### Rejected

- NULL duration lets transcribe bypass the cap — false: the download step stores the duration before transcribe is checked, and new submissions store it at admission.
- Zero-estimate steps bypass `check_budget` — documented decision (a step that costs nothing cannot go over); `Blocked` sizing returns 0 only when the Transcript is unreadable, and the step then fails itself.
- Guard raising a non-`StepError` leaves the Job `running` — low: only a database error, handled like any other worker error and recovered at the next start.
- Retry checks `active` before `not_failed`, so a paused Job's notice does not mention Resume — low: wording, both 409.
- Resume check not atomic with the requeue — low: the worker re-checks at every step boundary.
- `attempts` counts resumes and recoveries, and a guard-failed step stays at 1 — low: shown as "Attempts", counts starts.
- Admission refuses a $0 estimate at the cap while the guard allows it — matches Story 3.3 ("refused for the same reason", spec "anything").
- No lookup for `post_live` or playlists, `retry_notice` naming, CSRF, magic number 18, test dependence on real prices, lookup-test strictness — low or cosmetic for a single-user localhost app.
