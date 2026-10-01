---
title: 'Story 3.1: Recover after sleep or restart'
type: 'feature'
created: '2026-10-01'
status: 'done'
baseline_commit: '09bc90e'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-1-context.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** If the app is stopped, crashes, or the laptop sleeps mid-Job, the Job stays `running` forever (nothing picks it up), half-written temp files stay behind, and a brief network drop while waiting on Anthropic, OpenAI or the AssemblyAI upload fails the step and wastes the work.

**Approach:** At start-up, before the worker starts, every Job left `running` goes back to `queued`, its `running` steps go back to `pending`, and leftover `*.tmp` files in its Episode folder are removed. The worker then resumes it like any queued Job: finished steps are skipped, a saved AssemblyAI job ID is polled rather than the audio resubmitted, and an interrupted LLM call is simply made again. Transient network and vendor errors on vendor calls are retried with backoff up to a configured limit before the step fails.

## Boundaries & Constraints

**Always:**
- `app/core/recovery.py` provides `recover(conn, data_dir)`: in one transaction (`BEGIN IMMEDIATE`) set every `running` Job to `queued` and each of its `running` steps to `pending`; then remove `*.tmp` files directly inside the Episode folder of each recovered Job (never other files, never other Episodes' folders, never directories). It returns how many Jobs were recovered. It is idempotent and a no-op when nothing is `running`. A step's stored `vendor_job_id` is kept. Jobs in `queued`, `paused`, `failed` or `done` are never touched. A Job's `done` and `skipped` steps are never touched.
- It is called once from `app/main.py` after the database is bootstrapped and before `worker.start()`. It is not called from `create_app` or by the worker loop (a Job running in this process must never be reset). One line is printed at start when Jobs were recovered ("Recovered N interrupted Job(s)").
- Recovered Jobs keep their place by Job id (oldest first, as AD-6). Spend already recorded stays; no ledger rows are added, changed or removed by recovery.
- Resume behaviour relies on existing pipeline rules (a step is skipped only when stored `done` and its artifact exists). The transcribe step with a saved `vendor_job_id` polls that job and never submits the audio again; its cost is recorded once (the existing unique ledger reference). A summarize or verify call interrupted before it returned left no ledger row, so redoing it records one row; only completed calls appear in the ledger.
- `app/core/retry.py`: `call_with_backoff(fn, is_transient, retries, base_delay, max_delay, sleep=time.sleep)` calls `fn`, and on an exception for which `is_transient` is true waits `min(base_delay * 2**attempt, max_delay)` and calls again, at most `retries` times; any other exception, or the last transient one, is raised unchanged. `sleep` is injectable for tests.
- Config `[retry]` in `config.toml`: `max_retries` (default 4, integer 0 to 10), `base_delay_seconds` (default 5, positive), `max_delay_seconds` (default 60, positive, not below the base). Missing section uses the defaults; invalid values give a clear `ConfigError`, as other sections do.
- Applied to: the Anthropic and OpenAI vendor calls (retrying the one failed call, only for the adapters' own `Transient` kind: network, timeout, rate limit, vendor 5xx), and the AssemblyAI audio submit and upload (same). Not applied to authentication, refused, too-long or other permanent errors. The existing AssemblyAI polling backoff and yt-dlp's own retries are unchanged. A call that finally still fails raises the same `StepError` as today.
- Only completed calls are recorded in the ledger: a failed attempt records nothing, a retry that succeeds records one row.
- Waiting between attempts must not block shutdown: `Worker.stop` still returns within its existing 5 second wait.
- No secret or transcript text appears in logs or messages added by this story.

**Never:**
- No change to pipeline step order, the One-Pager, Verdicts or scores. No retry button or UI (Story 3.2), no cap checks or pausing (Stories 3.3 and 3.4). No recovery of `paused` or `failed` Jobs. No running of recovery while the worker is running. No new status or Job state. No retry of a paid call that returned an answer (including one that failed validation).

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Job left running | Job `running`, step `transcribe` `running` | After start-up: Job `queued`, step `pending`, earlier `done` steps untouched | N/A |
| Temp files | `audio.mp3.tmp` etc. in a recovered Job's folder | Removed; final files and other folders untouched | Missing or locked file: ignored, recovery continues |
| Other states | Jobs `queued`, `paused`, `failed`, `done` | Unchanged | N/A |
| Nothing to do | No running Job | No change, nothing printed | N/A |
| Twice | `recover` run twice | Second run changes nothing | N/A |
| Two Jobs | Two running Jobs | Both queued; run in id order | N/A |
| Resume transcribe | Saved vendor job ID, restart | Same vendor job polled; audio not submitted again; one ledger row | N/A |
| Interrupted LLM call | Kill during summarize or verify | Call made again on resume; one ledger row for it | N/A |
| Kill mid-step, fakes | Fake adapter killed mid-step, restart, worker runs | Job `done`; finished steps not repeated; no duplicate ledger entries | N/A |
| Transient then ok | Vendor call fails transiently twice then succeeds | Step succeeds; delays 5 s then 10 s (injected clock); one ledger row | N/A |
| Transient forever | Fails transiently more than `max_retries` times | Step fails with today's message; `max_retries` waits made | N/A |
| Permanent error | Auth, refused or too-long error | No retry; fails as today | N/A |
| Delay cap | Many retries | Delay never exceeds `max_delay_seconds` | N/A |
| `max_retries = 0` | Transient error | Fails at once, as today | N/A |
| Bad config | `max_retries = -1` or `max_delay < base` | Clear config error | N/A |
| No section | `[retry]` absent | Defaults used | N/A |
| Shutdown while waiting | Stop during a backoff wait | Worker stops within its normal wait | N/A |

</frozen-after-approval>

## Code Map

- `app/main.py` -- `run()` bootstraps the database, then builds adapters and starts the `Worker`; the recovery call goes between bootstrap and `worker.start()`. `app/worker.py` -- `Worker.run_next`, `stop`.
- `app/core/pipeline.py` -- `run_job` (skips `done` steps with their artifact; only `Exception` marks a step failed, so a `BaseException` simulates a kill), `StepResume`. `app/store/episodes.py` -- `set_job_state`, `set_step_state` (leaves `vendor_job_id` alone), `get_vendor_job_id`, `ACTIVE_JOB_STATES`. `app/store/artifacts.py` -- `episode_dir`, `temp_path`.
- `app/adapters/anthropic.py` (`_map`, call site near line 307), `app/adapters/openai.py` (near line 322), `app/adapters/assemblyai.py` (`_submit`, near line 275; polling backoff at `_wait` stays). `app/config.py` -- `_positive`, `load_config`; `config.toml`; `tests/fake_config.toml`.
- New: `app/core/recovery.py`, `app/core/retry.py`, `tests/test_recovery.py`.

## Tasks & Acceptance

**Execution:**
- [ ] `app/core/recovery.py`, `app/main.py` -- recovery and the start-up call -- recovery
- [ ] `app/core/retry.py`, `app/config.py`, `config.toml` -- backoff helper and `[retry]` settings -- retry
- [ ] adapters -- wrap the vendor calls and the AssemblyAI submit with the helper -- retry
- [ ] `tests/test_recovery.py` -- every matrix row with fakes, temp data and injected sleep; a kill-and-restart run -- verification

**Acceptance Criteria:**
- Given a Job left `running`, when the app starts, then it is `queued` again, its `running` step is `pending`, and leftover `.tmp` files in its folder are gone.
- Given a Job with a saved vendor job ID, when it resumes, then that job is polled, the audio is not submitted again, and the ledger has one entry for the work.
- Given an interrupted summarize or verify, when the Job resumes, then the call is made again and only completed calls are in the ledger.
- Given a brief network failure on a vendor call, when it recurs fewer times than the limit, then the step succeeds after backing off; beyond the limit it fails as it does today.
- Given a fake adapter killed mid-step and a restart, then the Job completes, finished steps are not repeated, and the ledger has no duplicates.

## Verification

**Commands:**
- `uv run pytest -q` -- expected: all tests pass offline

## Implementation Notes

- Built as specified: `app/core/recovery.py` (called from `app/main.py` after bootstrap, before the worker), `app/core/retry.py` (`call_with_backoff` and a `Retry` settings object), `[retry]` in `config.toml` and `tests/fake_config.toml`, `tests/test_recovery.py`.
- Judgment calls: adapters take a `retry` argument that defaults to no retries, so only `from_config` (the real app) retries and existing tests are unchanged. Retry wraps each single vendor call (Anthropic `_invoke`, OpenAI `_call`, AssemblyAI `_submit`) for the adapters' own `Transient` kind only. Recovery also clears a recovered step's old message and retryable flag. A temp-file sweep covers files only, not directories.
- Kill tests raise a `BaseException` from an adapter so the pipeline cannot record a failure, leaving the Job `running` as a real kill would. The kill happens before the adapter does work, so "ran again" is shown by the Job completing with every step's work done once.
- Not done: no real-network test of a sleep/wake.
- Review patches applied: backoff waits are stop-aware (`Stopping` is a `BaseException`, so a shutdown mid-wait leaves the Job `running` for recovery; `Worker.stop` wakes the wait), `main.run()` recovery wiring tests, `from_config` retry pass-through test, `RuntimeError` caught around recovery.

### Review Findings

- [x] [Review][Decision] (resolved: keep the retry; the duplicate needs a timeout after the vendor created the job, rarer than the network drop the retry exists for) AssemblyAI submit retried on a transient error can create a second paid job — `SdkClient.submit` uploads and creates the transcript in one SDK call, so a timeout after the vendor created the job looks identical to one before it; the retry then submits again, the first job is billed by the vendor but never reaches the ledger [app/adapters/assemblyai.py:_submit]
- [x] [Review][Patch] Backoff waits cannot be interrupted: `Worker.stop` joins for 5 s while a wait can last 60 s (spec: waiting must not block shutdown; matrix row has no test). Use a stop-aware wait and leave the Job `running` (recovered at next start) [app/core/retry.py, app/worker.py]
- [x] [Review][Patch] Start-up recovery wiring in `main.run()` is untested: moving or deleting the `recover` call, or breaking its error branch, passes every test [tests/test_startup.py]
- [x] [Review][Patch] No test that each real adapter's `from_config` passes `[retry]` through; removing it from one adapter silently disables retries in production [tests/test_recovery.py]
- [x] [Review][Patch] `main.run()` recovery block catches `OSError` and `sqlite3.Error` but `db.connect` can also raise `RuntimeError` (bootstrap already catches it), giving a traceback instead of the friendly exit [app/main.py]
- [x] [Review][Defer] Two app instances at once: the second one's start-up `recover()` resets a Job the first is genuinely running, and its worker may start it before the port bind fails — deferred: needs a single-instance lock, a new mechanism beyond this story

#### Rejected

- Retry-After and jitter ignored — low: single user, one request at a time; the fix adds branches and parameters.
- AssemblyAI polling not wrapped — false: polling already has its own backoff (`MAX_TRANSIENT_RETRIES`), and the spec leaves it unchanged.
- Requeued Job with a `failed` step — false: the pipeline sets a step `failed` and the Job `failed` together, so a `running` Job never has one.
- `*.tmp` cleanup may delete a partial needed for resume — false: `atomic_target` already removes the temp at the start of every run; nothing resumes from a temp file.
- Kill tests do not show a call made again / duplicate ledger — low: kills before work, as noted in the spec; `test_kill_mid_step_then_restart_completes` already shows finished steps are not repeated.
- Resume-transcribe checked only at the adapter level — low: the adapter and the stored vendor ID are tested separately and the pipeline path is unchanged.
- `Retry()` default of 0 differs from the config default of 4, shared default instance, test helper splitting on literals, unbounded delay, startup `print` vs `log`, unused `video_id` — low or cosmetic.
- Startup exit on database error not in the spec — informational.
