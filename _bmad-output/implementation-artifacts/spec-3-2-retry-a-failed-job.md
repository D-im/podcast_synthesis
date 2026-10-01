---
title: 'Story 3.2: Retry a failed Job'
type: 'feature'
created: '2026-10-01'
status: 'done'
baseline_commit: '062e400'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-1-context.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** When a step fails, the Episode page shows the error and nothing else. The only way forward is a new Job, which is not possible for an existing Episode, so a transient failure (a network drop, a rate limit) strands the Episode even though the earlier steps worked and were paid for.

**Approach:** A failed Job whose failed step is marked retryable shows a Retry button. Clicking it puts the same Job back in the queue with the failed step pending. The worker runs it like any queued Job: steps already `done` are skipped (so no new spend for them) and it restarts at the failed step, resuming a saved AssemblyAI job where one exists. The page shows how many attempts the failed step has had. A failure marked not retryable shows its error and an explanation instead of a button.

## Boundaries & Constraints

**Always:**
- Migration 9 adds `steps.attempts INTEGER NOT NULL DEFAULT 0`. `episodes.set_step_state` increments it whenever a step is set to `running` and leaves it alone for every other state. Existing rows keep 0; a failed step with 0 attempts is shown as 1 attempt. Recovery (Story 3.1) resetting a step to `pending` does not reduce it.
- `app/store/episodes.py` gets `retry_failed_job(conn, video_id)`: one transaction (`BEGIN IMMEDIATE`) that re-reads everything inside it and returns one of `queued`, `no_episode`, `no_job`, `not_failed`, `not_retryable`, `active`. It acts only on the Episode's latest Job, and only when that Job is `failed`, has a failed step whose `retryable` is 1, and no Job of the Episode is `queued`, `running` or `paused`. On `queued` it sets that step to `pending` (message and retryable cleared, `vendor_job_id` and `attempts` kept) and the Job to `queued`. Nothing else is touched: other steps, artifacts, One-Pager versions, scores, Verdicts and the ledger are unchanged. Every other outcome writes nothing.
- `POST /episodes/{video_id}/retry` (no body fields) calls it. `queued` redirects 303 to the Episode. `no_episode` is 404. Any other outcome is 409 and shows the Episode page with a plain message ("This Episode has no failed Job to retry.", "This failure cannot be retried.", "This Episode already has a Job waiting or running."). A GET never changes anything; there is no GET route for retry.
- The retried Job keeps its Job id, so it waits its turn in submission order (AD-6), behind any Job queued before it. A double click or two tabs retry once; the second gets the 409 `active` message.
- Resume uses the existing pipeline unchanged: a step is skipped only when stored `done` and its artifact exists; the failed step and later ones run; a saved AssemblyAI job ID is polled, not resubmitted. The ledger gets no row for skipped steps. Only new paid calls add rows.
- Episode page, in the failed state: the failed step's name, its error message (escaped), and the attempt count for that step as "Attempts: N". When retryable: a Retry button (a POST form). When not retryable: no button and the text "This failure is permanent, so retrying would fail the same way. It usually needs a different link or a changed setting." plus the error message. A failed Job with no failed step shows neither.
- Budget: the retry is queued like any Job and every paid step runs through the same worker and pipeline path, so the budget check that Stories 3.3 and 3.4 add before paid steps applies to retried steps with no further work. This story adds no cap check and no pausing.
- All stored text is HTML-escaped. The page reads the database on each request.

**Never:**
- No automatic retry, no retry of `done`, `paused` or `queued` Jobs, no creating a new Job, no retry of a not-retryable failure, no editing of artifacts, no resetting of steps that are `done`, no cap checks or pausing (Stories 3.3 and 3.4), no change to adapters, pipeline step order or what a first run does. No ledger writes at retry time.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Failed, retryable | Job `failed`, step failed with retryable | Page shows the step, message, attempts and a Retry button | N/A |
| Retry | POST with the above | Same Job `queued`, failed step `pending`, other steps untouched; redirect to Episode | N/A |
| Resume | Worker runs the retried Job | Steps `done` skipped; restarts at the failed step; ends `done`; no ledger rows for skipped steps | N/A |
| Not retryable | Step failed with retryable 0 | Error and explanation shown, no button; POST changes nothing | 409 |
| Unknown retryable | Failed step with retryable NULL | Treated as not retryable | 409 |
| Attempts | Step fails, retried, fails again | Shows "Attempts: 2", then 3 after another | N/A |
| Old data | Failed step with attempts 0 | Shown as 1 attempt | N/A |
| Not failed | Job `queued`, `running`, `paused` or `done` | No button; POST changes nothing | 409 |
| Active elsewhere | A newer Job of the Episode is active | POST changes nothing | 409 |
| No Job | Episode without a Job | POST changes nothing | 409 |
| Unknown Episode | Bad video ID | 404 | N/A |
| Double submit | POST twice | One requeue; second gets the message | 409 |
| Queue order | Another Job queued first | The retried Job runs after it | N/A |
| Saved vendor ID | Failed transcribe with a saved ID | Retry polls that vendor job, audio not submitted again | N/A |
| Failed verify after a new version | Job failed at verify | Retry runs only verify; One-Pager versions unchanged | N/A |
| Fails again | Retried step fails again | Job `failed` again with the new message and a higher attempt count | N/A |
| Hostile text | Error message with markup | Escaped | N/A |
| GET | GET `/episodes/{id}/retry` | 404 or 405, nothing changes | N/A |

</frozen-after-approval>

## Code Map

- `app/store/migrations.py` -- add migration 9. `app/store/episodes.py` -- `set_step_state`, `get_latest_job_with_steps`, `get_steps`, `has_active_job`, `enqueue_regeneration` (pattern for an atomic check-and-write); add `retry_failed_job`, expose `attempts` in step dicts.
- `app/core/pipeline.py` -- `run_job` already skips finished steps and reruns from the failed one; no change expected. `app/web/status.py` -- `describe_job` (failed step, message); add retry fields. `app/web/app.py` -- `load()`, routes; `app/web/templates/_status.html`.
- New: `tests/test_retry_job.py`. Existing tests that assert the schema version (now 9) and the startup table list's columns need the number bumped.

## Tasks & Acceptance

**Execution:**
- [ ] `app/store/migrations.py`, `app/store/episodes.py` -- attempts column and counter, atomic `retry_failed_job` -- persistence
- [ ] `app/web/status.py`, `app/web/app.py`, `_status.html` -- failed-step details, Retry form, POST route and messages -- UI
- [ ] `tests/test_retry_job.py` -- every matrix row with fakes, including a worker run showing skipped steps add no ledger rows -- verification

**Acceptance Criteria:**
- Given a failed Job with a retryable failed step, when I open it, then I see the step, the error, the attempts and a Retry button.
- Given I click Retry, when the Job runs, then steps already `done` are skipped, it restarts at the failed step, and the ledger has no new rows for skipped steps.
- Given a failure marked not retryable, when I open it, then the error is shown with an explanation and no Retry button.
- Given repeated failures, when I view the Job, then I see how many attempts the failed step has had.
- Given a retry of a paid step, when it is queued, then it takes the same worker and pipeline path as any paid step, so the cap check added by Stories 3.3 and 3.4 will cover it.

## Verification

**Commands:**
- `uv run pytest -q` -- expected: all tests pass offline

## Implementation Notes

- Built as specified: migration 9 (`steps.attempts`, counted when a step is set `running`), `episodes.retry_failed_job` (one transaction, returns the outcome), `POST /episodes/{id}/retry`, failed-step details, Retry form and permanent-failure text in `_status.html`, `tests/test_retry_job.py`.
- Judgment calls: the retry notice is shown only on the 409 page. Schema-version assertions moved from 8 to 9 and one migration test gained `attempts`. No cap check, as specified (Stories 3.3 and 3.4).
- Not done: no code-review pass yet; story is in `review`.
