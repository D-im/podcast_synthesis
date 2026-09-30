---
title: 'Story 1.3: Run a Job end to end on fake providers'
type: 'feature'
created: '2026-09-30'
status: 'done'
baseline_commit: '6cdaf7bd27d7ccc1e223e8ea11db1a0897b12653'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-1-context.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Jobs are created but nothing runs them. The pipeline, step state handling and provider boundaries must be proven with no network and no spend before any real API is wired in.

**Approach:** Define the four provider ports and deterministic zero-cost fake adapters. Add a single worker thread that runs queued Jobs serially, step by step, persisting step state and writing artifacts atomically to `data/episodes/<video_id>/`. Failures store a sanitized message and a `retryable` flag. Provider choice comes from `config.toml`, which now defaults every role to `fake` so the whole app runs end to end locally.

## Boundaries & Constraints

**Always:**
- Ports live in `app/ports/` as `typing.Protocol`s with result types: `Downloader.download(video_id, url, dest) -> DownloadResult(title, duration_seconds)`, `Transcriber.transcribe(audio_path) -> Transcript`, `Summarizer.summarize(transcript) -> OnePager(sections)`, `Verifier.verify(transcript, one_pager) -> VerificationResult(accuracy, coverage, unsupported_claims, missed_ideas)`. Adapters raise `StepError(message, retryable)` for expected failures.
- `app/core` and `app/web` import only `app/ports`, `app/store` and stdlib. Never `app/adapters` or a vendor SDK (`yt_dlp`, `assemblyai`, `anthropic`, `openai`). Only `app/worker.py` imports adapters and wires them to ports by config name.
- Persisted step state is the source of truth. States: `pending`, `running`, `done`, `failed`, `skipped`. Job states: `queued`, `running`, `done`, `failed`. A step is skipped on re-run only when its state is `done` and its artifact file exists.
- Only the worker thread changes step and Job state after creation. One Job at a time, oldest queued first (AD-3, AD-6).
- Artifacts: write to a temp file in the same folder, flush, `os.replace` to the final name, then mark the step `done`. Files: `audio.m4a`, `transcript.json`, `one_pager.json`, `verification.json` under `data/episodes/<video_id>/`. All file and SQL access is in `app/store`.
- The download result's title and duration are saved on the Episode.
- If the Verifier is not configured (`None`), the `verify` step ends `skipped` with a reason and the Job still ends `done`.
- Stored error messages are truncated to 500 characters and have any configured API key value redacted. Unexpected (non-`StepError`) exceptions store only the exception class name and `retryable=1`. Logs and messages never contain transcript text.
- `config.toml` providers default to `fake` for `downloader`, `transcriber`, `summarizer`, `verifier`; real names stay as comments. The validated roles now include `downloader`.
- Migration 2 adds `message TEXT` and `retryable INTEGER` to `steps`.

**Never:**
- No real vendor adapters, metering or ledger, cost fields used, status polling UI, retry, recovery, cap, or prompt files (later stories).
- No concurrent Jobs, no in-memory-only state, no writes to `steps` from `app/web`.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Happy path | One `queued` Job, all fakes | Steps run `download`, `transcribe`, `summarize`, `verify`; each goes `pending`, `running`, `done`; Job `done`; four artifacts exist; Episode title and duration set | N/A |
| Serial order | Two queued Jobs | Second starts only after first ends; oldest first | N/A |
| Skip finished | Step `done` with artifact, Job requeued | That step's adapter is not called | N/A |
| Done but file missing | Step `done`, artifact deleted | Step re-runs | N/A |
| Expected failure | Fake fails at `transcribe` with `retryable=True` | `transcribe` `failed` with message and `retryable=1`; Job `failed`; later steps stay `pending`; `download` stays `done` | Message sanitized |
| Non-retryable failure | `StepError(retryable=False)` | Stored `retryable=0` | N/A |
| Unexpected exception | Adapter raises `ValueError("secret transcript text")` | Step `failed`, message is `ValueError` only, `retryable=1` | No text leaked |
| Secret in message | `StepError` text contains a configured key value | Stored text has it redacted | N/A |
| Failure mid-write | Adapter raises after partial write | No final file, no temp file left | Step `failed` |
| No verifier | Verifier `None` | `verify` `skipped` with reason; Job `done` | N/A |
| Layering | Source of `app/core`, `app/web`, `app/ports` | No import of `app.adapters` or vendor SDKs | Test fails otherwise |

</frozen-after-approval>

## Code Map

- `app/store/db.py`, `app/store/migrations.py`, `app/store/episodes.py` -- connection, migration list (add migration 2), existing queries. `get_latest_job_with_steps` returns steps without `message` or `retryable`; extend it.
- `app/core/submit.py` -- owns `STEP_NAMES`; reuse it for the pipeline step order.
- `app/main.py` -- `run()` builds config, bootstrap, `create_app`; start and stop the worker here.
- `config.toml`, `app/config.py` (`ROLES`), `tests/test_startup.py` -- config roles and tests reference provider names and will need updating for `fake` and `downloader`.
- New: `app/ports/` (`__init__.py`, result types), `app/adapters/fakes.py`, `app/core/pipeline.py`, `app/store/artifacts.py`, `app/worker.py`.
- Do not change the web routes or templates in this story.

## Tasks & Acceptance

**Execution:**
- [ ] `app/ports/` -- four Protocols, `StepError`, and result types (`DownloadResult`, `Transcript` with timestamped segments, `OnePager` with ordered named sections, `VerificationResult`) -- provider boundary
- [ ] `app/adapters/fakes.py` -- deterministic zero-cost fakes; `FakeBehavior` options to fail a named step (retryable or not), raise an unexpected exception, or leave a partial write; a `cost_micro` field defaulting to 0 (unused until Story 1.5) -- test doubles used by every later story
- [ ] `app/store/migrations.py` -- migration 2: `steps.message`, `steps.retryable` -- failure persistence
- [ ] `app/store/artifacts.py` -- episode folder path, temp path, atomic promote, cleanup of temp on failure -- atomic artifact writes
- [ ] `app/store/episodes.py` -- queue queries (next queued Job), Job and step state updates, set Episode metadata, read steps with message and retryable -- all SQL
- [ ] `app/core/pipeline.py` -- run one Job: ordered steps, skip rule, state transitions, sanitizing, `skipped` when no Verifier -- the step state machine
- [ ] `app/worker.py` -- `build_adapters(config)` mapping provider names to fakes, `Worker` with `run_next()` and start/stop thread polling the queue, serial -- composition root
- [ ] `app/config.py`, `config.toml` -- add `downloader` role; providers default to `fake` (real values kept as comments) -- config
- [ ] `app/main.py` -- start the worker thread before serving and stop it on shutdown -- wiring
- [ ] `tests/` -- every matrix row using `run_next()` with fakes and temp dirs, plus one thread test that submits two links through the web app and waits for both Jobs to finish; update existing config tests for the new roles -- verification

**Acceptance Criteria:**
- Given a queued Job and fake adapters, when the worker runs, then all four steps end `done`, the Job ends `done`, and the four artifacts exist under `data/episodes/<video_id>/`.
- Given two queued Jobs, when the worker runs, then they run one after the other in submission order.
- Given a step that failed, when its record is read, then it holds a sanitized message and a `retryable` flag, and later steps are still `pending`.
- Given a finished step with its artifact, when the Job runs again, then that step is skipped.
- Given the source tree, when `app/core`, `app/web` and `app/ports` are scanned, then none imports `app.adapters` or a vendor SDK.
- Given the whole suite, when it runs, then it needs no network and records no spend.

## Implementation Notes

- Built as specified: `app/ports/` (Protocols, `StepError`, result types with dict round-trip, `Adapters`), `app/adapters/fakes.py`, `app/core/pipeline.py`, `app/store/artifacts.py`, `app/worker.py`, migration 2, config roles, worker wiring in `app/main.py`, `tests/test_pipeline.py`.
- Judgment calls: `[models]` does not require `downloader`; `verifier = "none"` yields a `None` Verifier (used to exercise the `skipped` path); any provider other than `fake` makes startup exit with a configuration error until real adapters land in Stories 1.6 to 1.8.
- Review patches: once a step really runs, all later steps re-run (no stale artifacts from an older transcript); a downloader that writes nothing fails with a clear retryable message; artifact paths reject unsafe video IDs; worker shutdown waits at most 5 seconds; tests added for `main.run()` wiring (worker starts, processes a job, stops even if the server raises, unavailable provider exits 1) and for upgrading a populated v1 database to v2.
- Not done by design: crash recovery, retry, failure display on the episode page (Stories 3.1, 3.2, 1.4).

## Spec Change Log

## Review Triage Log

| Finding | Verdict | Route | Evidence |
|---|---|---|---|
| Upstream step re-runs but later `done` steps keep stale artifacts | medium | patch | Skip rule only looked at each step alone; now later steps re-run |
| Downloader writes no file gives opaque `FileNotFoundError` | low | patch | Direct one-line check with a clear message |
| Unsafe video ID could reach artifact paths | low | patch | Defense in depth; `episode_dir` now rejects it |
| Migration 2 untested on populated v1 database | medium | patch | Only fresh DBs were tested; test added |
| `main.run()` worker start/stop and unavailable-provider exit untested | medium | patch | Deleting `start()`/`stop()` left the suite green; three tests added |
| Worker `stop()` joins with no timeout, Ctrl-C hangs during a long step | medium | patch | Now `join(timeout=5)` |
| Requeue test could pass vacuously | low | patch | Asserts state is `queued` first; also `time.monotonic` |
| Job or step left `running` after a crash or exception; no startup reconciliation | high if unmanaged | rejected | Intent excludes recovery; Story 3.1 owns it |
| DB write failure between steps leaves Job `running` | medium | rejected | Same: Story 3.1 recovery requeues stale `running` Jobs |
| Unexpected exceptions always `retryable=1` | low | rejected | Stated in the spec |
| Redaction only covers REQUIRED_KEYS exact strings | low | rejected | Acceptable for the three known keys |
| Failure message and retryable not shown on episode page | low | rejected | Story 1.4 (status display) |
| Step/metadata writes not one transaction | low | rejected | Re-run is idempotent |
| No compare-and-set on `running`; thread-safety of start/stop | low | rejected | Single worker by design (AD-6) |
| Commits not explicit | false | none | Connections use `isolation_level=None` (autocommit) |
| Missing step row silently skipped; missing index; fake `partial_write` ignores `fail_at`; cost_micro unused; int vs bool retryable; style nits | low | rejected | No named harm |
| Existing configs lacking `downloader` fail | low | rejected | Single-user, no deployed configs |

## Verification

**Commands:**
- `uv run pytest -q` -- expected: all tests pass
- `uv run podcast-synthesis`, submit a watch URL, reload `/episodes/<id>` -- expected: Job and all four steps reach `done` within a few seconds, and `data/episodes/<id>/` holds four files
