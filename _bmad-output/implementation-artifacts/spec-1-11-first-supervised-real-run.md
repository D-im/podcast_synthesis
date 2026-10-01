---
title: 'Story 1.11: First supervised real run'
type: 'chore'
created: '2026-09-30'
status: 'done'
baseline_commit: '435b37f5f19795ddf7592e1ce806ac025cb17820'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-1-context.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Every real vendor path has only been checked with small smoke tests. The core assumption (a one-pager is good enough to trust instead of listening) and the real cost of an hour of audio are still unknown, and the Daily Cap needs real numbers.

**Approach:** Switch the default providers to the real ones, run one real one-hour episode end to end through the running app, and record what happened: cost per step, wall-clock time per step, transcript and one-pager stats, any failures, and an informal faithfulness check of the one-pager against the transcript. Fix small bugs the run exposes. Write the results to one findings file.

## Boundaries & Constraints

**Always:**
- Episode: `https://www.youtube.com/watch?v=B7yl7fEHeKM` (about 65 minutes, 3929 seconds), submitted through the app's own form or `/submit`, not through a script that bypasses the pipeline.
- `config.toml` defaults become `downloader = "yt-dlp"`, `transcriber = "assemblyai"`, `summarizer = "anthropic"`, `verifier = "none"` (the real Verifier arrives in Epic 2, so `verify` ends `skipped` with its reason and the Job still ends `done`). `tests/fake_config.toml` keeps every provider `fake`; the parity test is updated to ignore the `[providers]` table only.
- Before starting: confirm nothing is listening on the app's port, use a fresh `data/` folder, and read keys only from `.env` (the app loads it). Never print, log or commit keys. Do not delete or stop anything this story did not start.
- Cost ceiling: stop and investigate if the ledger total for the Episode passes $1.50. Total spend for the whole story, including retries, must stay under $3.
- Because retry does not exist yet (Epic 3), if a step fails after money was spent, recover by putting the Job back to `queued` directly in the database. Finished steps are skipped and a saved vendor job ID is resumed, so nothing is paid twice. Record that this was done.
- Record in `_bmad-output/implementation-artifacts/real-run-1-11.md`: the cost per step and total from the ledger next to the pre-run estimates (about $0.25 transcription at $0.23 per hour for 3929 s; one-pager tokens and cost); wall-clock time per step; transcript segment count, speaker count and total words; the one-pager sections; which prompt hashes and model were stored; the status the Episode page showed throughout; and every problem met, with how it was handled.
- Faithfulness check: read the one-pager against the Transcript and list a sample of at least 8 specific claims with a verdict each (supported, unsupported, distorted), plus any major idea in the Transcript the one-pager missed. State plainly whether the one-pager is good enough to trust, and what would change that.
- Log check: search the app's console log and any log files for the three API key values and for distinctive transcript phrases; report none found (or fix and re-run the check).
- Recommend a Daily Cap from the measured cost (the number stays configurable and the decision is Diimrem's; PRD open question 2).
- Small bugs found (a wrong default, a bad message, a crash) are fixed in this story with a test; anything larger goes to `deferred-work.md`.

**Never:**
- No Verifier or OpenAI use, cap enforcement, retry UI or recovery code. No second episode unless the first fails before producing a Transcript. No loosening of validation to make the run pass. Do not commit `data/` or `.env`.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Happy run | Real keys, the episode above | Job `done`; real Transcript with speakers; One-Pager v1; `verify` `skipped` with reason; ledger rows for `transcribe` and `summarize` | N/A |
| Cost | Finished run | Ledger and Episode page show per-step and total spend matching each other | Mismatch is a bug |
| Step failure after paying | A real step fails | Job set back to `queued`; finished steps skipped; vendor job resumed; no second transcription charge | Recorded in the findings |
| Over ceiling | Ledger above $1.50 | Stop and investigate before continuing | N/A |
| Logs | After the run | No key value and no transcript phrase in console or files | Fix and re-check |
| Offline suite | After the config change | Still passes with the network blocked | N/A |

</frozen-after-approval>

## Code Map

- `config.toml` -- defaults to flip (currently `yt-dlp` plus fakes). `tests/fake_config.toml` -- all-fake copy used by offline tests; `tests/test_ytdlp.py::test_fake_config_matches_real_config_except_downloader` compares the two files and must change to ignore `[providers]`.
- `app/main.py` -- `run()` loads `.env`, builds adapters, starts the worker and server; `app/worker.py` `build_adapters` accepts `assemblyai`, `anthropic`, `verifier = "none"`.
- Ledger table `spend_ledger`; per-step spend and status on `/episodes/<id>` and `/episodes/<id>/status`; artifacts under `data/episodes/<video_id>/` (`transcript.json`, `one_pager.v1.json`). Keys live in `.env` (git-ignored).
- Smoke tests already passed against each real service on short inputs; this is the first full run on a long episode.

## Tasks & Acceptance

**Execution:**
- [ ] `config.toml`, `tests/test_ytdlp.py` -- flip the default providers; parity test ignores `[providers]` -- real defaults, offline suite intact
- [ ] Run the app, submit the episode through it, watch status until `done` or failure, recover per the rules if needed -- the supervised run
- [ ] `_bmad-output/implementation-artifacts/real-run-1-11.md` -- costs, timings, stats, claim check, log check, cap recommendation, problems and fixes -- the deliverable
- [ ] Fix small bugs found, with tests; record larger ones in `deferred-work.md` -- hardening

**Acceptance Criteria:**
- Given real keys and the episode, when it is submitted, then the Job reaches `done` with a real Transcript and One-Pager.
- Given the finished run, when the spend is read, then the findings file shows the actual cost per step and in total, matching the ledger.
- Given the One-Pager, when checked against the Transcript, then the findings file lists at least 8 claim verdicts and a plain statement of whether it can be trusted.
- Given the logs, when searched, then no API key or transcript text appears.
- Given the changed defaults, when the offline suite runs, then it passes.

## Implementation Notes

- The run and its results are in `real-run-1-11.md`: cost $0.3098 for a 65.5 minute episode (transcribe $0.2511, summarize $0.0587), 28 claims checked against the transcript (26 supported, 1 softened, 0 invented), no key or transcript text in logs, Daily Cap recommendation, and the problems found.
- Defaults are now real: `downloader = "yt-dlp"`, `transcriber = "assemblyai"`, `summarizer = "anthropic"`, `verifier = "none"`. `tests/fake_config.toml` keeps every provider `fake`; the parity test ignores `[providers]`.
- Scope note: the run showed uploads to AssemblyAI running at about 75 KB/s (a 59 MB file failed after about 16 minutes with an opaque error). Beyond the "small bug" fixes in the spec, a small feature was added because it was the fix: the adapter re-encodes a temporary mono 16 kHz AAC copy for upload (`[assemblyai] compress_before_upload`, `upload_bitrate_kbps`, 16 to 128), about 3.5 times smaller, with the original untouched and a logged fallback to the original. Step failure messages now carry the vendor's reason (SDK and HTTP-library text only, URLs removed, key redacted).
- Recovery during the run was done by hand (Job set back to `queued` with the transcription job ID on the step), which also proved the resume design for real.
- Real-API smoke tests (yt-dlp, AssemblyAI, Claude single call, Claude map-reduce) all pass after the changes.

## Spec Change Log

## Review Triage Log

| Finding | Verdict | Route | Evidence |
|---|---|---|---|
| Compression failure falls back silently | medium | patch | Warning logged with the reason (ffmpeg's stderr tail) and tested |
| Compressed copy bigger than the original is uploaded | low | patch | Only used when smaller |
| `_describe` could show a key in vendor text | medium | patch | The configured key is redacted; test |
| Temp copy name `audio.m4a.m4a`; 30 minute ffmpeg timeout | low | patch | Name `.upload-audio.m4a`; timeout 600 s |
| Bitrate lower bound of 8 kbps unmeasured for speech | low | patch | Lower bound 16; bool rejected |
| `from_config` upload settings, argv, missing ffmpeg, generic submit branch, real config yields no verifier: untested | medium | patch | Tests added |
| Truncated compressed output after a clean ffmpeg exit | low | rejected | ffmpeg exit code is checked; a timeout or error falls back |
| Temp file shared by concurrent runs of the same audio | low | rejected | One worker at a time (AD-6) |
| `resume.save` failure orphans a paid job | low | rejected | `set_vendor_job_id` now raises if the step row is missing |
| ffmpeg test needs macOS `say`; README and docs not updated | low | rejected | The argv test runs everywhere; README is already deferred |
| Defaults flipped to real providers | low | rejected | Required by the story; offline tests use `fake_config.toml` and the network is blocked |

## Verification

**Commands:**
- `uv run pytest -q` -- expected: all tests pass offline
- `sqlite3 data/podcast_synthesis.db "select step, provider, amount_micro_usd from spend_ledger"` -- expected: a `transcribe` row near 251000 and a `summarize` row of a few tens of thousands
- Open `/episodes/B7yl7fEHeKM` -- expected: status Done, spend shown, One-Pager sections, Transcript link, audio player
