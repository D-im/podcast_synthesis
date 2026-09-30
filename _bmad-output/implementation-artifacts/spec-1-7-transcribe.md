---
title: 'Story 1.7: Transcribe'
type: 'feature'
created: '2026-09-30'
status: 'done'
baseline_commit: 'bd05331b582a309c25461f4f6f23fa860f15dcaf'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-1-context.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** `transcribe` only has a fake. The pipeline needs a real, timestamped Transcript of up to 6 hours of audio, with its cost recorded, and a way to resume a vendor job instead of paying twice.

**Approach:** Add an AssemblyAI adapter for the `Transcriber` port. It submits the audio as one file, saves the vendor job ID before waiting, polls until the job finishes, records the cost once, and returns a normalized Transcript with speaker labels. The pipeline gets a small resume handle backed by the database. The Episode page links to a readable Transcript page. `config.toml` keeps `transcriber = "fake"` as the default, so nothing spends money until the provider is switched to `assemblyai` (Story 1.11).

## Boundaries & Constraints

**Always:**
- Use the `assemblyai` SDK (pinned at the verified 1.6.1). Submit the file once without splitting (`Transcriber.submit`), then poll `Transcript.get_by_id`. Use `speech_models` from `[models] transcriber` and `speaker_labels` from config. Check the installed SDK for exact argument shapes.
- Ports: `Transcriber.transcribe(audio_path, meter, resume)`. `Resume` Protocol: `load() -> str | None`, `save(vendor_job_id)`, `clear()`. The pipeline supplies a step-bound handle backed by `steps.vendor_job_id` (migration 4). Re-running a step never clears the stored ID.
- Flow: if `resume.load()` returns an ID, poll that job and do not submit. Otherwise submit, `resume.save(id)` immediately, then poll. If the vendor reports the job as failed, `resume.clear()` so a retry submits fresh.
- Cost: when a job completes, record `audio_duration` (seconds) times `[pricing] transcription_usd_per_hour` (default 0.23, covering diarization) in micro-dollars, round half up, through the meter with `ref` set to the vendor job ID, before building the Transcript. If the vendor gives no duration, use the last segment's end time.
- `Meter.record(provider, amount_micro_usd, ref=None)`. Migration 4 adds `spend_ledger.provider_ref` with a unique index on `(video_id, step, provider, provider_ref)` where `provider_ref` is not null. Appends with a `ref` use `INSERT OR IGNORE`, so re-fetching a finished job never records it twice.
- Normalized Transcript: `Segment(start, end, text, speaker=None)` in seconds. With speaker labels on, one segment per utterance with `speaker` like `A`; otherwise sentence-level segments. `Segment.from_dict` tolerates a missing `speaker`.
- Polling: interval and overall wait from config (`[assemblyai] poll_interval_seconds` default 5, `max_wait_minutes` default 180). Up to 5 consecutive transient poll errors are retried with backoff; more fails the step as retryable with the vendor job ID kept. A wait timeout is retryable with the ID kept.
- Errors to `StepError`, never containing the key: missing `ASSEMBLYAI_API_KEY` (retryable, names the variable, nothing submitted); rejected key (retryable, says so); vendor job error such as no speech or unsupported audio (non-retryable, vendor text); network failures on submit (retryable).
- Web: `GET /episodes/{video_id}/transcript` renders segments as `[mm:ss or h:mm:ss] Speaker: text`, all escaped, 404 if the artifact does not exist. The status fragment links to it once `transcribe` is `done`.
- `build_adapters` accepts `transcriber = "assemblyai"` and still `fake`. New config keys are validated.
- Tests never use the network. One `network`-marked smoke test runs only with `RUN_NETWORK_TESTS=1` and a real key, on a few seconds of speech generated with macOS `say` and ffmpeg (skipped if `say` is missing).

**Never:**
- No splitting or stitching of audio, no chunking, no language configuration, no webhook, no cost estimator or cap (Epic 3). No changes to `download`, `summarize` or `verify`. Do not flip the default provider. Do not log or store the API key or transcript text.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Happy path | Scripted client: queued, processing, completed | Vendor ID saved before the first poll; one ledger row with `ref`=ID; normalized Transcript written; step `done` | N/A |
| Cost math | 3600 s and 21600 s at 0.23/h | 230000 and 1380000 micro-dollars | N/A |
| Resume | Saved vendor ID present | No submit; same ID polled | N/A |
| Re-fetch finished job | Completed ID fetched twice | Still one ledger row | Duplicate ignored |
| Vendor job error | Status error "no spoken audio" | `StepError` non-retryable with vendor text; saved ID cleared | Step `failed` |
| Missing key | No `ASSEMBLYAI_API_KEY` | Retryable `StepError` naming the variable; nothing submitted | No key in messages |
| Rejected key | 401 from vendor | Retryable `StepError` about the key | Step `failed` |
| Transient poll errors | 2 failures then success | Completes normally | N/A |
| Persistent poll errors | 6 consecutive failures | Retryable `StepError`; ID kept | Step `failed` |
| Wait timeout | Never completes within `max_wait_minutes` | Retryable `StepError`; ID kept | Step `failed` |
| Submit failure | Network error on submit | Retryable `StepError`; no ID saved | Step `failed` |
| Large file | 6 h file | One submit call, no splitting | N/A |
| Speaker off | `speaker_labels = false` | Sentence segments, `speaker` is None | N/A |
| Transcript page | Before and after transcribe | 404, then escaped text with timestamps and speakers; link shown only when done | N/A |
| Upgrade | Populated v3 database | Migration 4 applies; data intact | N/A |

</frozen-after-approval>

## Code Map

- `app/ports/__init__.py` -- `Transcriber`, `Meter`, `Segment`, `Transcript`; add `Resume`, the `resume` argument, `Meter.record(..., ref=None)`, and `Segment.speaker`.
- `app/adapters/fakes.py` -- `FakeTranscriber` takes the new argument. `app/core/pipeline.py` -- builds the step meter and calls adapters in `run_step`; add the resume handle and pass it to `transcribe`.
- `app/core/meter.py` -- `StepMeter.record`; `app/store/spend.py` -- `append`; `app/store/migrations.py` (list ends at migration 3); `app/store/episodes.py` -- step queries (store vendor ID here).
- `app/worker.py` -- `build_adapters`; `app/config.py` -- config validation and `Config`; `config.toml` and `tests/fake_config.toml` (kept in step by a test).
- `app/web/app.py`, `templates/_status.html` -- add the transcript route and link. `app/store/artifacts.py` -- `read_json`, `TRANSCRIPT`.
- New: `app/adapters/assemblyai.py` (adapter plus a thin SDK client class that tests replace), `app/web/templates/transcript.html`, `tests/test_assemblyai.py`.
- `pyproject.toml` -- add the pinned dependency. Tests that call adapters directly need the new arguments.

## Tasks & Acceptance

**Execution:**
- [ ] `pyproject.toml` -- add `assemblyai==1.6.1` -- dependency
- [ ] `app/store/migrations.py`, `app/store/spend.py`, `app/store/episodes.py` -- migration 4 (`steps.vendor_job_id`, `spend_ledger.provider_ref` plus unique index); idempotent append; vendor ID get, set, clear -- persistence
- [ ] `app/ports/__init__.py`, `app/core/meter.py`, `app/core/pipeline.py`, `app/adapters/fakes.py` -- `Resume` port, `ref` on `record`, `Segment.speaker`, pipeline resume handle, fake updated -- wiring
- [ ] `app/adapters/assemblyai.py` -- `AssemblyAITranscriber` with an injectable client; flow, cost, normalization, polling and error mapping per the rules -- adapter
- [ ] `app/config.py`, `config.toml`, `tests/fake_config.toml`, `app/worker.py` -- `[assemblyai]` and `[pricing]` keys validated, `assemblyai` provider accepted, default stays `fake` -- config
- [ ] `app/web/` -- transcript route, template, status link -- viewing
- [ ] `tests/test_assemblyai.py` and updates -- every matrix row with a scripted fake client; migration 4 on a populated v3 database; the smoke test -- verification

**Acceptance Criteria:**
- Given audio, when `transcribe` runs, then the vendor ID is stored before polling begins and a normalized Transcript is saved.
- Given a stored vendor ID, when the step re-runs, then the audio is not submitted again and the ledger gains no duplicate row.
- Given a completed job, when the step finishes, then exactly one ledger row holds the cost, computed from the audio duration and the configured price.
- Given a vendor error, missing key or rejected key, when the step runs, then it fails with a readable message that contains no secret and the correct `retryable` flag.
- Given a finished Episode, when its Transcript page is opened, then timestamped, speaker-labelled text is shown, escaped.
- Given the full offline suite, when it runs, then it passes with networking blocked.

## Implementation Notes

- Built as specified: `assemblyai==1.6.1`, migration 4 (`steps.vendor_job_id`, `spend_ledger.provider_ref` with a partial unique index), `Resume` port and `StepResume`, `Meter.record(..., ref=None)`, `Segment.speaker`, `app/adapters/assemblyai.py`, config keys `[assemblyai]` and `[pricing]`, `/episodes/{id}/transcript`, `tests/test_assemblyai.py`.
- Deviation: `Transcript.get_by_id` blocks until the job finishes, so polling uses the SDK's own HTTP client (`GET /v2/transcript/{id}`). After completion, utterances and sentences are read through `aai.Transcript(id, client=...).wait_for_completion()`, which returns after one request. This relies on SDK internals pinned at 1.6.1. Additions beyond the spec: a 404 on a saved job ID clears it and fails retryable; a non-401 4xx on submit is non-retryable.
- Not exercised against the real service yet (no API key set here): `audio_duration` on a real job, the exact shape of a real vendor error, and `SdkClient` against real responses. Check these first in the Story 1.11 run, or run `RUN_NETWORK_TESTS=1 ASSEMBLYAI_API_KEY=... uv run pytest -m network`.
- Known small window: a crash after the vendor accepts the upload but before its job ID is saved resubmits on the next run. Nothing more can be done client-side.
- Review patches: ledger appends with a `ref` use `ON CONFLICT DO NOTHING` (other constraint violations still raise); `set_vendor_job_id` raises if the step row is missing; empty speaker turns fall back to sentences; a finished job with no speech fails permanently but is still billed; a vanished finished job clears the ID; a vendor error status returned by `submit` is permanent; HTTP 408 and 425 are transient; a non-object poll body and an unknown status state are treated as transient faults; a corrupt transcript artifact gives a 404; new tests cover the SDK mapping layer with stubs and a full worker success path (saved ID, ledger row with `provider_ref`, speaker in the artifact, resume without a second submit or ledger row).

## Spec Change Log

## Review Triage Log

| Finding | Verdict | Route | Evidence |
|---|---|---|---|
| `SdkClient` status mapping and parsing untested offline | high | patch | Regressions (401 to transient, ms not divided) would pass; stub-based tests added |
| Worker success path never ran real `StepMeter` and `StepResume` together | high | patch | Test now checks stored ID, ledger `provider_ref`, speaker, no re-pay |
| `INSERT OR IGNORE` swallows any constraint violation and drops cost rows | high | patch | `ON CONFLICT ... DO NOTHING` for ref rows only; violation tests |
| `set_vendor_job_id` silently no-ops when the step row is missing | medium | patch | Raises `LookupError` |
| Empty utterances saved as an empty Transcript | medium | patch | Falls back to sentences; no speech is a permanent, billed failure |
| Finished job vanishes (404) while fetching the transcript | medium | patch | Clears ID, retryable |
| Submit returns an error-status transcript without raising | medium | patch | Mapped to permanent refusal |
| 408 and 425 treated as permanent | low | patch | Now transient |
| Non-object poll body, unknown status state polled for hours | low | patch | Transient fault, fails after 5 |
| Corrupt transcript artifact gives a 500 | low | patch | 404 |
| Crash between vendor accept and saving the ID resubmits | low | rejected | Inherent; noted in Implementation Notes |
| Fallback cost from last segment end can under-record | low | rejected | Vendor always reports `audio_duration`; fallback is a last resort |
| Polling relies on SDK internals | medium | rejected | Pinned version; real-call check listed for Story 1.11 and the opt-in test |
| Non-HTTP exceptions mapped to transient | low | rejected | Finite retries, then a retryable step failure |
| Wait can overrun by one interval; `Refused` while polling treated as transient; fake `resume` optional; naming; README | low | rejected | No named harm; README already deferred |
| Migration 4 could fail half-way | false | none | Each migration is one transaction with its version bump |

## Verification

**Commands:**
- `uv run pytest -q` -- expected: all tests pass offline
- `RUN_NETWORK_TESTS=1 ASSEMBLYAI_API_KEY=... uv run pytest -q -m network` -- expected: a few seconds of generated speech comes back as text; cost under a cent
- Set `transcriber = "assemblyai"` in `config.toml` and run the app on a short video -- expected: real Transcript page and a ledger row
