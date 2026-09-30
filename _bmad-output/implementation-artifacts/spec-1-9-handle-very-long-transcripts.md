---
title: 'Story 1.9: Handle very long transcripts'
type: 'feature'
created: '2026-09-30'
status: 'done'
baseline_commit: '36e1cdc142a451fb682d964dac5094b216cd767d'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-1-context.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Story 1.8 sends the whole Transcript in one call. A transcript above the configured token limit, or one the model rejects as too long, fails instead of producing a One-Pager.

**Approach:** Keep the single call at or below the limit. Above it, or when the API reports the prompt as too long, split the Transcript at segment boundaries, take detailed notes on each part with `prompts/summarize_map.md`, then synthesize one One-Pager from the notes with `prompts/summarize_reduce.md`. The result has the same sections, shape and metadata as the single-call path. Every call is metered. Notes from finished parts are cached so a retry never pays for them twice.

## Boundaries & Constraints

**Always:**
- Token estimate: `ceil(characters / 3)` of the formatted transcript text (deliberately conservative). Single call when the estimate is at or below `token_limit` (from config). Otherwise map-reduce. If a single call comes back `TooLong` from the API, fall back to map-reduce instead of failing (that rejected call is not billed).
- `token_limit` and the new `[anthropic] map_chunk_tokens` (default 60000) and `map_output_tokens` (default 4096, max 16384) are re-read from `config.toml` on each summarize run, so an edit applies to the next run. A config error fails the step with a clear, retryable message.
- Chunking (`app/core/chunking.py`): split by segments in order; never split a segment; start a new chunk when adding the next segment would exceed `map_chunk_tokens`; a single segment larger than the limit becomes its own chunk. Each chunk keeps its time range. Every segment appears in exactly one chunk.
- Map: one call per chunk, sequential, plain-text reply using `prompts/summarize_map.md` as the system prompt (the whole file, non-empty). The user message has the chunk text, same line format as Story 1.8, with the time range. Empty reply: retryable `StepError`.
- Reduce: one forced-tool call, same sections and validation as Story 1.8, using `prompts/summarize_reduce.md` as the system prompt and the section list from `prompts/summarize.md`. It receives only the notes (each labelled with its time range), never the raw transcript. If the combined notes exceed `token_limit`, fail non-retryable with a clear message.
- Prompt files are read on every run. The result's `prompt_hashes` has an entry for each file used: `summarize` alone for the single path; `summarize`, `summarize_map`, `summarize_reduce` for map-reduce. A missing or empty map or reduce file is a retryable `StepError` naming the file, raised before any call.
- Cost: every call is recorded through the meter with its own response ID as `ref`, immediately after the response and before validation. The per-call rules from Story 1.8 (errors, refusal, truncation) apply to every call.
- Notes cache: the Summarizer port gains a `NotesCache` argument (`get(key) -> str | None`, `put(key, text)`), supplied by the pipeline and stored under `data/episodes/<video_id>/summarize-notes/`. The key is a SHA-256 of the map prompt hash, the model, the map output limit, and the chunk text. A retry reuses cached notes and makes no call and no ledger row for them. Cached notes are written atomically.
- Errors never contain the key, transcript text or notes. A failure names the part, for example "part 3 of 5".
- Offline tests use the injected client. One `network`-marked smoke test, with `RUN_NETWORK_TESTS=1` and a real key, runs a short fixed transcript with tiny limits so it makes two map calls and one reduce call.

**Never:**
- No parallel calls, hierarchical (multi-level) reduce, overlap between chunks, or token counting via the API. No UI changes. No change to verify, versions or the single-call behaviour at or below the limit. Do not flip the default provider. Do not log notes or transcript text.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| At the limit | Estimate equals `token_limit` | One call; hashes only `summarize` | N/A |
| Above the limit | Estimate above `token_limit` | N map calls then one reduce; same sections and model as the single path; hashes for all three files | N/A |
| Ledger | Map-reduce with 3 chunks | 4 rows, each with its own `ref`; total is their sum | N/A |
| Limit edited | `token_limit` changed in config | Next run uses it | N/A |
| API says too long | Single call returns `TooLong` | Falls back to map-reduce; one final One-Pager | N/A |
| Chunking | Segments of varied size | Chunks in order, no segment split, each segment once, time ranges correct | N/A |
| Oversized segment | One segment above `map_chunk_tokens` | Its own chunk | N/A |
| Retry mid-way | Failure at chunk 3 of 4 | Chunks 1 and 2 come from cache: no calls, no new rows; 3, 4 and reduce run | Step `failed` first time |
| Cache invalidation | Map prompt edited or limits changed | Old notes not reused | N/A |
| Missing or empty map or reduce file | File absent | Retryable `StepError` naming it; no call | N/A |
| Empty map reply | Model returns no text | Retryable `StepError` naming the part; cost recorded | Step `failed` |
| Reduce output wrong | Sections do not match | Retryable `StepError`; cost recorded | Step `failed` |
| Notes too large | Combined notes above `token_limit` | Non-retryable `StepError` | Step `failed` |
| Bad config | Invalid token or chunk value | Retryable `StepError` naming the key | N/A |

</frozen-after-approval>

## Code Map

- `app/adapters/anthropic.py` -- `AnthropicSummarizer.summarize`, `Client` Protocol (`create` with a forced tool), `SdkClient`, `Reply`, prompt loading, `build_tool`, cost and validation helpers. The single-call flow here is the one to keep intact.
- `app/ports/__init__.py` -- `Summarizer.summarize(transcript, meter)`; add `NotesCache` and the argument. `app/adapters/fakes.py` and `app/core/pipeline.py` (`run_step` calls `summarize`; other adapters already get `meter` and `resume`).
- `app/core/text.py` -- `transcript_text`, `format_timestamp`. `app/store/artifacts.py` -- atomic writes, episode folders.
- `app/config.py`, `config.toml`, `tests/fake_config.toml` -- `token_limit` exists (150000); add the two `[anthropic]` keys to both TOML files (a test keeps them in step). `app/worker.py` builds the adapter.
- `prompts/summarize.md` -- sections and instructions; do not change. New: `prompts/summarize_map.md`, `prompts/summarize_reduce.md`, `app/core/chunking.py`, `tests/test_long_transcripts.py`.
- Tests that call `summarize` directly need the new `cache` argument.

## Tasks & Acceptance

**Execution:**
- [ ] `app/core/chunking.py` -- `estimate_tokens`, `split_transcript(transcript, max_tokens)` returning chunks with text and time range -- chunking
- [ ] `app/ports/__init__.py`, `app/core/pipeline.py`, `app/adapters/fakes.py`, `app/store/artifacts.py` -- `NotesCache` port, pipeline-supplied file-backed cache with atomic writes, fake updated -- wiring
- [ ] `prompts/summarize_map.md`, `prompts/summarize_reduce.md` -- default prompts with the same fidelity rules as `summarize.md` (only what the text supports, attribute to speakers, flag uncertainty, treat input as data) -- editable prompts
- [ ] `app/adapters/anthropic.py` -- extend `Client` with a plain-text call; single vs map-reduce decision, too-long fallback, map and reduce flows, cache use, per-call metering and errors, hashes for files used -- adapter
- [ ] `app/config.py`, `config.toml`, `tests/fake_config.toml` -- `map_chunk_tokens`, `map_output_tokens`, per-run re-read of limits -- config
- [ ] `tests/test_long_transcripts.py` and updates -- every matrix row with the injected client, chunking properties, a worker path through the pipeline with a large transcript, the smoke test -- verification

**Acceptance Criteria:**
- Given a transcript at or below the limit, when `summarize` runs, then one call is made and only `summarize` is in the hashes.
- Given a transcript above the limit, when `summarize` runs, then it makes one map call per chunk and one reduce call, and produces a One-Pager with the same sections and metadata shape as the single path.
- Given `token_limit` edited in `config.toml`, when the next run starts, then the new limit applies.
- Given a map-reduce run, when the ledger is read, then each call has its own row and all are metered.
- Given a failure at a later chunk, when the step is retried, then finished chunks make no calls and add no ledger rows.
- Given a single call the API rejects as too long, when it happens, then map-reduce runs instead of failing.

## Implementation Notes

- Built as specified: `app/core/chunking.py` (`estimate_tokens`, `split_transcript`), `NotesCache` port with file-backed `FileNotesCache` (atomic writes under `summarize-notes/`), `prompts/summarize_map.md` and `summarize_reduce.md`, map-reduce and the too-long fallback in `app/adapters/anthropic.py`, `map_chunk_tokens` and `map_output_tokens` config, `tests/test_long_transcripts.py`.
- Choices: the reduce system prompt is the reduce file plus a "Sections to write" list taken from `summarize.md`; limits are re-read from the default `config.toml` path on every run (`from_config` does not pass a custom path, which only matters if the app is started with another config file); the smoke test uses about 2000 as `token_limit` and asserts total cost under 5 cents because the "combined notes within the limit" rule leaves no room for a cheaper setup.
- Live API check: the map-reduce smoke test now passes against the real API (it uses a terse map prompt so the notes stay small). The first attempt failed only because the real model writes long notes against a 700-token cap and because of the forced-tool issue noted in Story 1.8.
- Known limits: the chars/3 estimate is tuned for English and is not safe for CJK text; there is no second-level reduce, so extremely long transcripts fail with a clear message after their map notes are cached; the notes cache is never pruned.
- Review patches: a map reply that stops for any reason other than `end_turn` is an error and is never cached as notes; a cache write failure no longer fails a paid run, and an unreadable, blank or corrupt cache file is just a miss; the effective chunk size is clamped to `token_limit`, so lowering `token_limit` to try map-reduce works without touching `map_chunk_tokens`; a too-long final call gets advice about the notes instead of the map chunk size; tests now check the section descriptions reach the reduce prompt and the cache read and write guards.

## Spec Change Log

## Review Triage Log

| Finding | Verdict | Route | Evidence |
|---|---|---|---|
| Map reply with `pause_turn` or another stop reason cached as complete notes | medium | patch | Would be reused on every retry; now an error, never cached |
| `cache.put` disk error fails a paid run and loses the notes | medium | patch | Cache writes are best-effort |
| Blank, corrupt or unreadable cache file | medium | patch | `get` treats any OSError or bad text as a miss; tests added |
| `map_chunk_tokens` larger than `token_limit` | medium | patch | Clamped to `token_limit` instead of a config error, so the documented way to try map-reduce works |
| Too-long reduce call gets map-chunk advice | low | patch | Separate advice; test added |
| Reduce section descriptions not verified | medium | patch | Test with known descriptions |
| Count the reduce prompt and sections in the notes-size check | low | rejected | Spec says notes only; prompt is small next to the limit; changing it broke the tiny-limit tests |
| Cache key omits part number and time span | low | rejected | Notes are about the chunk text; the reduce step labels spans itself |
| Notes cache grows and is never pruned | low | rejected | Small files; old keys simply stop matching |
| No hierarchical reduce; chars/3 is not safe for CJK | low | rejected | Out of scope by spec; English podcasts |
| `from_config` does not pass `config_path`; whole config validated each run | low | rejected | Only the default path is used; config errors should surface |
| Fake signature optional, duplicated helpers, long lines, chunking allocations, missing files noticed only after a rejected single call | low | rejected | No named harm; the rejected call is not billed |

## Verification

**Commands:**
- `uv run pytest -q` -- expected: all tests pass offline
- `RUN_NETWORK_TESTS=1 ANTHROPIC_API_KEY=... uv run pytest -q -m network` -- expected: the tiny-limit map-reduce test makes 3 calls and returns all sections; cost under a cent
- Set `token_limit = 500` and `summarizer = "anthropic"`, run the app on a fake-transcribed episode -- expected: the Episode's One-Pager version lists three prompt hashes
