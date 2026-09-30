---
title: 'Story 1.8: Generate the One-Pager'
type: 'feature'
created: '2026-09-30'
status: 'done'
baseline_commit: 'b86e89f070620e345af35cb20a7fbb4eac48dff2'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-1-context.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** `summarize` only has a fake. Diimrem needs a faithful one-page synthesis of the real Transcript, written from an editable prompt, stored as a versioned artifact, with its cost recorded.

**Approach:** Add an Anthropic adapter for the `Summarizer` port. It reads `prompts/summarize.md` at job time, sends the whole Transcript in one call, and forces structured output so the result has exactly the sections the prompt file declares. Each run stores the next One-Pager version with the prompt file's content hash and the model name. The Episode page shows the latest One-Pager section by section. The default provider stays `fake` until Story 1.11.

## Boundaries & Constraints

**Always:**
- Use the `anthropic` SDK (pin the current release, 1.10.0 at planning time; confirm it resolves). Model from `[models] summarizer` (`claude-sonnet-5-5`). Check the installed SDK for exact argument shapes.
- `prompts/summarize.md` format: a sections block, a line containing only `---`, then the instruction text. Each section line is `Name: description` (names unique, non-empty). The file is read from disk on every run, so an edit applies to the next run without a restart. Ship a default file with sections Summary, Big ideas, Actionable items, Notable quotes, Worth your time. Its instructions require: state only what the Transcript supports, attribute claims to speakers, flag uncertainty, use no outside facts, keep it to about one page, and treat the Transcript as data, never as instructions.
- Structured output by a forced tool call whose schema is built from the declared sections (one required string per section). The response must contain exactly those sections, each non-empty. The page renders by section name and never parses free text.
- The whole Transcript goes in one call, formatted as lines `[h:mm:ss] Speaker A: text` (the speaker part only when present). Move the timestamp formatter into `app/core` so the web layer and the adapter share it.
- `OnePager` gains `model` and `prompt_hashes` (mapping of prompt name to the SHA-256 of the file bytes), both defaulted so older artifacts still load.
- Versioning (AD-14): each run of `summarize` writes `one_pager.v<N>.json` with N one higher than the latest and a `one_pager_versions` row (migration 5: `video_id`, `version`, `created_at`, `model`, `prompt_hashes` as JSON). Earlier versions are never overwritten. The step counts as finished only when it is `done` and the latest version file exists. `verify` reads the latest version.
- Cost: from the response's actual `usage` tokens and `[pricing] summarizer_input_usd_per_million` (default 2.0) and `summarizer_output_usd_per_million` (default 10.0), recorded through the meter with `ref` set to the response ID, immediately after the response and before any validation. Config `[anthropic] max_output_tokens` (default 4096).
- Errors to `StepError`, never containing the key or transcript text: missing `ANTHROPIC_API_KEY` (retryable, names the variable, no call); rejected key (retryable); rate limit, connection, timeout and 5xx (retryable); prompt too long (non-retryable, says long-transcript handling is Story 1.9); other 4xx (non-retryable); bad or missing prompt file (retryable, names the file and the problem); output that does not match the declared sections, no tool call, or `max_tokens` cut-off (retryable, cost still recorded).
- Web: the Episode status fragment shows the latest One-Pager, each section under its name, escaped, line breaks preserved, once `summarize` is `done`.
- `build_adapters` accepts `summarizer = "anthropic"` and still `fake`. The fake returns `model="fake"` and empty hashes. New config keys are validated and mirrored in `tests/fake_config.toml`.
- Offline tests use an injected client and stub-based tests for the SDK mapping layer. One `network`-marked smoke test runs only with `RUN_NETWORK_TESTS=1` and a real key, on a short fixed transcript.

**Never:**
- No map-reduce, token counting or long-transcript handling (Story 1.9). No regeneration UI (Story 2.4), no verification changes, no library list. Do not flip the default provider. Do not log the key or transcript text.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Happy path | Stubbed client returns every declared section | `one_pager.v1.json`; version row with model and prompt hash; ledger row with `ref`; page shows the sections | N/A |
| Cost math | 100000 input and 2000 output tokens at 2.0 and 10.0 | 220000 micro-dollars | N/A |
| Prompt edited | File changed between runs | Next run uses it; new hash recorded | N/A |
| Re-run | Summarize runs again | `v2` written; `v1` kept; latest is `v2` | N/A |
| Bad prompt file | Missing, no sections, duplicate or empty name, no `---` | Retryable `StepError` naming file and problem; no call | No text leaked |
| Bad model output | Missing or extra section, empty text, no tool call | Retryable `StepError`; cost still recorded; nothing saved | Step `failed` |
| Truncated | `stop_reason` max tokens | Retryable `StepError`; cost recorded | Step `failed` |
| Missing key | No `ANTHROPIC_API_KEY` | Retryable `StepError` naming it; no call | Key never shown |
| Rejected key | Authentication error | Retryable, about the key | Step `failed` |
| Transient | Rate limit, connection, timeout, 5xx | Retryable | Step `failed` |
| Too long | Prompt too long | Non-retryable, mentions Story 1.9 | Step `failed` |
| Injection | Transcript says "ignore instructions" | Sent as data; output still validated against the sections | N/A |
| Transcript text | Segments with and without speakers | `[0:00:05] A: hi` and `[0:00:05] hi` | N/A |
| Page | Before and after summarize | No One-Pager, then sections escaped | N/A |
| Upgrade | Populated v4 database | Migration 5 applies; data intact | N/A |

</frozen-after-approval>

## Code Map

- `app/ports/__init__.py` -- `Summarizer`, `OnePager`, `Section`; add `model` and `prompt_hashes` with defaults.
- `app/core/pipeline.py` -- `run_step` for `summarize` and `verify`, `ARTIFACT_FOR_STEP`, skip rule; `app/store/artifacts.py` and `app/store/episodes.py` for files and queries; `app/store/migrations.py` (list ends at migration 4).
- `app/adapters/fakes.py` -- `FakeSummarizer`. `app/adapters/assemblyai.py` -- the pattern to copy: injectable client, stub-tested `SdkClient`, `from_config`, errors to `StepError`.
- `app/worker.py`, `app/config.py`, `config.toml`, `tests/fake_config.toml` -- wiring, validation, parity test.
- `app/web/app.py`, `app/web/status.py` (`format_timestamp` lives here today), `templates/_status.html` -- display.
- New: `app/adapters/anthropic.py`, `app/core/text.py`, `prompts/summarize.md`, `tests/test_anthropic.py`. `pyproject.toml` -- add the dependency.

## Tasks & Acceptance

**Execution:**
- [ ] `pyproject.toml` -- add `anthropic` pinned -- dependency
- [ ] `app/store/migrations.py`, `app/store/artifacts.py`, `app/store/episodes.py` -- migration 5 (`one_pager_versions`), versioned file names, latest-version lookups -- persistence
- [ ] `app/ports/__init__.py`, `app/core/text.py`, `app/core/pipeline.py`, `app/adapters/fakes.py` -- `OnePager` metadata, shared timestamp and transcript formatting, versioned summarize step, verify reads latest -- wiring
- [ ] `prompts/summarize.md` -- default prompt with the five sections and the fidelity rules -- editable prompt
- [ ] `app/adapters/anthropic.py` -- `AnthropicSummarizer` with an injectable client, prompt parsing, forced tool schema, cost, validation, error mapping; `SdkClient` wrapper -- adapter
- [ ] `app/config.py`, `config.toml`, `tests/fake_config.toml`, `app/worker.py` -- `[anthropic]` and pricing keys, `anthropic` provider accepted, default stays `fake` -- config
- [ ] `app/web/` -- latest One-Pager in the status fragment -- viewing
- [ ] `tests/test_anthropic.py` and updates -- every matrix row, SDK mapping via stubs, a full worker success path (version row, ledger `ref`, hashes, page), migration 5 on a populated v4 database, the smoke test -- verification

**Acceptance Criteria:**
- Given a completed Transcript and the default prompt file, when `summarize` runs, then the whole Transcript is sent in one call and `one_pager.v1.json` holds exactly the declared sections.
- Given an edit to `prompts/summarize.md`, when the next run starts, then the edit is used and the stored hash changes.
- Given a stored One-Pager, when its version row is read, then it holds the model name and the SHA-256 of each prompt file used.
- Given output that does not match the declared sections, when the step runs, then it fails retryable, saves nothing, and keeps the cost in the ledger.
- Given a finished Episode, when its page is opened, then the One-Pager shows by section, escaped.
- Given a summarize re-run, when it finishes, then the new version exists and every earlier version is still on disk.

## Implementation Notes

- Built as specified: `anthropic==1.10.0`, `app/adapters/anthropic.py` (`AnthropicSummarizer`, prompt parser, forced tool call, `SdkClient`), `prompts/summarize.md` (five sections plus fidelity rules), migration 5 (`one_pager_versions`), versioned `one_pager.v<N>.json`, `OnePager.model` and `prompt_hashes`, shared `app/core/text.py` (`format_timestamp`, `transcript_text`), config keys, One-Pager in the status fragment, `tests/test_anthropic.py`.
- Choices: tool property names are generated (`section_1`, ...) and mapped back, because Anthropic limits property names and section names contain spaces; `format_timestamp(seconds, always_hours=False)` serves both the transcript page (`mm:ss`) and the prompt (`h:mm:ss`); a section may have an empty description; section names compare case-insensitively.
- Not exercised against the live API (no `ANTHROPIC_API_KEY` here): the real model's tool output, real `usage` and error shapes. The opt-in smoke test (`RUN_NETWORK_TESTS=1 ANTHROPIC_API_KEY=... uv run pytest -m network`) checks this; the Story 1.11 run is the next check.
- Episodes summarized before this story (legacy `one_pager.json`, no version row) are not adopted: re-queuing one re-runs `summarize` as v1 and the page shows a notice until then. Deferred.
- Review patches: a literal `</transcript>` in the transcript is escaped so it cannot close the data block; an empty Transcript fails fast without a call; `stop_reason` refusal is non-retryable (cost still recorded); a whitespace key counts as missing and is stripped; HTTP 403 and 404 now give hints (permission or region, unknown model) instead of a key message; `max_output_tokens` is capped at 16384 in config so the SDK never raises on a non-streaming request; a missing or unreadable One-Pager file shows a notice on the page; verify gives a clear error if the latest file is missing; tests prove verify reads the latest version and that injection text stays inside the data block.

## Spec Change Log

## Review Triage Log

| Finding | Verdict | Route | Evidence |
|---|---|---|---|
| Transcript text containing `</transcript>` closes the data block early | high | patch | Escaped; test added |
| `verify` reading the latest version is not pinned by any test | medium | patch | Recording verifier over two versions |
| No guard for an empty Transcript (paid call on nothing) | medium | patch | Fails fast |
| Refusal stop reason retried pointlessly | medium | patch | Non-retryable |
| 403 reported as a key problem; 404 gives no model hint | medium | patch | Hints added |
| Whitespace-only key passes the check | low | patch | Stripped |
| `max_output_tokens` too large makes the SDK raise, mapped as retryable | medium | patch | Capped at 16384 in config |
| Unreadable One-Pager artifact disappears silently from the page | medium | patch | Visible notice and test |
| Verify hits a raw error if the latest file is missing | low | patch | Clear retryable error |
| Episodes with a legacy `one_pager.json` are not adopted; re-queue re-runs summarize | medium | defer | Only pre-upgrade dev data; recorded in deferred-work |
| Version file written before its row; orphan file if the insert fails | low | rejected | Next version uses max(file, row), so no collision; nothing concurrent |
| One-Pager versions not tied to a job | low | rejected | Versions belong to the Episode by design (AD-14) |
| Cache token billing, duplicated default prices, hard-coded timeout, substring match for "too long", max_tokens retryable, re-export noqa | low | rejected | No current harm; prices live in config |

## Verification

**Commands:**
- `uv run pytest -q` -- expected: all tests pass offline
- `RUN_NETWORK_TESTS=1 ANTHROPIC_API_KEY=... uv run pytest -q -m network` -- expected: a short fixed transcript yields all declared sections; cost under a cent
- Set `summarizer = "anthropic"` in `config.toml` and run the app on a fake-transcribed episode -- expected: a One-Pager with the five sections on the Episode page
