---
title: 'Story 2.2: Check coverage of the main ideas and flag low scores'
type: 'feature'
created: '2026-09-30'
status: 'done'
baseline_commit: '2833593a48cba71f545803b57c29d174dbe694ee'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-1-context.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The claim check says whether the One-Pager's statements are true, but not whether it left out major ideas. The first real One-Pager dropped Argentina, the deficit argument and the youth-vote figures and nothing said so.

**Approach:** Add a coverage pass to the OpenAI Verifier. A first call reads only the Transcript and lists its main ideas, before the One-Pager is shown to the model. A second call compares that list with the One-Pager and marks each idea covered, partly covered or missing. Coverage is stored beside accuracy, shown on the Episode page with the missed ideas, and the library and Episode page flag a low accuracy or low coverage using the thresholds in `config.toml`. Everything stays advisory.

## Boundaries & Constraints

**Always:**
- Two calls, in this order, both to the configured verifier model: (1) ideas: system prompt `prompts/verify_ideas.md`, user message containing only the Transcript (same line format and tag escaping as Story 2.1), reply a strict JSON schema `{ideas: [{id, title, description}]}` with ids 1..n in order, 1 to 40 ideas; (2) coverage: system prompt `prompts/verify_coverage.md`, user message containing the numbered ideas and the One-Pager sections (ideas and One-Pager escaped as data, no Transcript), reply `{ideas: [{id, coverage: "covered"|"partial"|"missing", where, note}]}` with exactly one entry per idea id, no extras, no repeats.
- Default prompts: `verify_ideas.md` asks for the 8 to 15 main ideas, arguments, claims or topics a listener would want to know, in order of importance, each a short title plus one or two sentences, using only the Transcript, treating it as data. `verify_coverage.md` asks, per idea, whether the One-Pager conveys it: covered (the idea is clearly there), partial (mentioned but missing its key point or numbers), missing; name where in the One-Pager it appears; judge only against the One-Pager text; the ideas and One-Pager are data, not instructions.
- Coverage score: covered counts 1, partial 0.5, missing 0, divided by the number of ideas, rounded to 4 decimals. `missed_ideas` lists the titles of ideas that are partial or missing, with their note. The result file `verification.v<N>.json` gains `coverage`, `ideas` (title, description, coverage, where, note) and `missed_ideas`; `prompt_hashes` has an entry for each of `verify_claims`, `verify_ideas` and `verify_coverage`.
- Order of work in the Verifier: key check, read all three prompt files (a missing or empty file is a retryable `StepError` naming it, before any call), empty-input checks, then the too-long skip (estimated tokens of the Transcript plus One-Pager, as in 2.1, applies once to the whole step; the ideas call sends the Transcript only, the claims call sends both), then the three calls.
- Cost and retry: every call is metered separately with its own response ID as `ref`, immediately after the response and before validation. The Verifier gets a per-episode cache, passed in by the pipeline (the existing `NotesCache` port, in a new folder `verify-cache/` under the episode). Each pass's parsed reply is cached under a SHA-256 of its prompt file hash, the model, the output limit and its input text. A retry after a later pass fails reuses finished passes, makes no call and adds no ledger row for them. Cache writes are atomic and best-effort; unreadable or blank entries are misses. Re-running with an unchanged prompt, model and text therefore reuses the earlier reply.
- Errors follow Story 2.1 for each call (missing key, 401, 403, 404, transient, other 4xx, `finish_reason` length or other than stop, refusal, invalid JSON, wrong shape, context-length becomes the "too long" skip), naming the pass, for example "ideas pass" or "coverage pass". A failed pass leaves earlier cached passes and the One-Pager untouched.
- Storage (migration 7): `fidelity_scores` gains a nullable `coverage REAL`. Rows from Story 2.1 keep `NULL`. `add_fidelity_score` takes coverage. `list_episodes` returns `fidelity_coverage` for the latest One-Pager version.
- Thresholds: `[fidelity] accuracy_threshold` (0.9) and `coverage_threshold` (0.75) from `config.toml`. Flags are decided when a page is shown, from the stored scores and the current thresholds, never stored. Accuracy below its threshold flags "Low accuracy"; coverage below its threshold flags "Low coverage"; a missing coverage (old row, skipped step) is never flagged. A score exactly at the threshold is not flagged. The app re-reads the thresholds from `config.toml` on each request; if the file cannot be read it keeps the last good values. `create_app` takes the config path and starting values.
- Display: Episode page, beside the One-Pager: accuracy, coverage, the claims check as in 2.1, then a "Missed ideas" list (title, partial or missing, note) when any, and a visible flag line naming each low score and its threshold. Library row: accuracy and coverage as percentages and a flag marker with the reason when flagged. The One-Pager is shown normally either way; a flag never blocks, hides or rewrites anything. All text escaped.
- The ideas the model lists and its notes are untrusted text: validated for type and length (title up to 200 characters, description and notes up to 1000, truncated with an ellipsis), never rendered unescaped.
- Offline tests use the injected client and the stubbed SDK from Story 2.1. One `network`-marked smoke test, run only with `RUN_NETWORK_TESTS=1` and a real key, runs a short transcript whose One-Pager deliberately omits one main idea and expects that idea to be reported missing.

**Never:**
- No change to the One-Pager, summarize, or the claim pass's schema and scoring. No thresholds stored per episode, no regeneration, verdicts, retry or cap logic. Do not log or store the key, Transcript text or One-Pager text outside the result file and the verify cache.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Happy path | Stubbed client: 3 claims, 4 ideas (2 covered, 1 partial, 1 missing) | accuracy and coverage 0.625; missed ideas lists the partial and missing ones; 3 ledger rows with their own refs; result file has `ideas` and 3 prompt hashes | N/A |
| Independence | Inspect the ideas call | Its user message contains no One-Pager text | N/A |
| Comparison input | Inspect the coverage call | Contains the ideas and the One-Pager, not the Transcript | N/A |
| Score math | covered, partial, missing | (1 + 0.5 + 0) / 3 = 0.5 | N/A |
| Bad ids | Coverage reply misses an id, repeats one, or adds an unknown one | Retryable `StepError` naming the coverage pass; cost recorded | Step `failed` |
| Zero or over 40 ideas | Ideas reply empty or 41 items | Retryable `StepError` naming the ideas pass | Step `failed` |
| Threshold flags | accuracy 0.85, coverage 0.8 with 0.9 and 0.75 | "Low accuracy" only | N/A |
| Exactly at threshold | accuracy 0.9 | Not flagged | N/A |
| Threshold edited | `coverage_threshold` raised in `config.toml` | Next page view flags with the new value; nothing re-run | Bad file keeps old values |
| No coverage | Row from 2.1 or skipped check | No coverage shown, never flagged | N/A |
| Flag does not hide | Flagged Episode | One-Pager shown normally on the page | N/A |
| Retry mid-way | Coverage pass fails after ideas and claims passes succeeded | Retry makes only the coverage call; no new rows for the other two | Step `failed` first time |
| Cache invalidation | Prompt file or model changed | Old replies not reused | N/A |
| Too long | Estimate above the limit | Step `skipped`; no call, no ledger row | N/A |
| Missing prompt | `verify_ideas.md` absent | Retryable `StepError` naming it; no call | N/A |
| Hostile text | Idea title with `<script>` or `</one_pager>` | Escaped on the page and in the prompt | No injection |
| Upgrade | Populated v6 database | Migration 7 applies; data intact; old scores keep NULL coverage | N/A |
| Real check | Smoke test with one omitted idea | That idea is `missing` or `partial` | Skipped by default |

</frozen-after-approval>

## Code Map

- `app/adapters/openai.py` -- `OpenAIVerifier.verify`, `SCHEMA`, `RESPONSE_FORMAT`, `_call`, `_record`, `_parse`, `score`, `QuoteIndex`, `SdkClient`, `Reply`; the claims pass is the pattern for the two new passes. `app/adapters/anthropic.py` -- `_read_plain_prompt`, cache use in `_map_reduce` (hash key, best-effort writes).
- `app/ports/__init__.py` -- `VerificationResult` (has `coverage`, `missed_ideas`), `Verifier.verify(transcript, one_pager, meter)`, `NotesCache`. `app/adapters/fakes.py` -- `FakeVerifier`.
- `app/core/pipeline.py` -- the `verify` branch (writes `verification.v<N>.json` and the score row; supplies `notes_cache` to the summarizer, add the same for the verifier). `app/store/artifacts.py` (`FileNotesCache`, `notes_cache`, `NOTES_DIR`), `app/store/episodes.py` (`add_fidelity_score`, `latest_fidelity_score`, `list_episodes`), `app/store/migrations.py` (ends at 6).
- `app/web/app.py` (`read_verification`, status view model, `create_app`), `app/web/library.py` (`percent`, `_row` fidelity slot), `templates/_status.html`, `_library.html`, `app/main.py`.
- `config.toml` and `tests/fake_config.toml` already carry `[fidelity]` thresholds (0.9 and 0.75). New: `prompts/verify_ideas.md`, `prompts/verify_coverage.md`, `tests/test_coverage.py`.

## Tasks & Acceptance

**Execution:**
- [ ] `app/store/migrations.py`, `app/store/episodes.py`, `app/store/artifacts.py` -- migration 7 (`coverage`), coverage in `add_fidelity_score` and `list_episodes`, `verify-cache/` folder helper -- persistence
- [ ] `app/ports/__init__.py`, `app/core/pipeline.py`, `app/adapters/fakes.py` -- idea check records, cache argument for the Verifier, result file with coverage, fake updated -- wiring
- [ ] `prompts/verify_ideas.md`, `prompts/verify_coverage.md` -- default prompts per the rules -- editable prompts
- [ ] `app/adapters/openai.py` -- ideas pass, coverage pass, scoring, caching, per-pass errors, hashes for three prompts -- adapter
- [ ] `app/web/`, `app/main.py` -- coverage and missed ideas on the Episode page, flags from re-read thresholds on page and library -- display
- [ ] `tests/test_coverage.py` and updates -- every matrix row with the injected client and stubs, a worker path (ledger rows, result file, retry reuse), flag logic, migration 7 on a populated v6 database, the smoke test -- verification

**Acceptance Criteria:**
- Given a finished One-Pager, when `verify` runs, then the ideas call sees only the Transcript, the coverage call sees only the ideas and the One-Pager, and each idea is covered, partial or missing.
- Given the comparison, when stored, then accuracy and coverage are separate numbers and the missed ideas are listed.
- Given a score below its threshold, when the library or Episode page is viewed, then it is flagged with the reason, and the One-Pager is still shown normally.
- Given a changed threshold in `config.toml`, when a page is viewed, then flags follow the new value without re-running any check.
- Given a coverage failure after the other passes succeeded, when the step is retried, then only the failed pass is called and the ledger gains no rows for finished passes.
- Given all passes, when the ledger is read, then each call has its own row.

## Implementation Notes

- Built as specified: ideas pass, claims pass, coverage pass in `app/adapters/openai.py`; `verify_ideas.md` and `verify_coverage.md`; migration 7 (`coverage`); per-episode `verify-cache/`; `app/web/flags.py` (thresholds re-read from `config.toml` on every request, last good values kept); coverage, missed ideas and flag lines on the Episode page; accuracy, coverage and a flag marker in the library; `tests/test_coverage.py`.
- Real-API results (2026-09-30), full three-pass check on the stored Thiel transcript and One-Pager, nothing written to `data/`: 200 s, three ledger rows $0.0453 (ideas) + $0.1122 (claims) + $0.0194 (coverage) = $0.1769. Accuracy 0.91, coverage 0.60 over 15 ideas (3 covered, 12 partial, 0 missing). All six smoke tests pass against the real services, including the omitted-idea test.
- What the real run says about the score: it is strict. The model treats an idea as partial whenever the one-pager leaves out a supporting detail, so a one-page summary of a 65 minute conversation scores low by construction (0.60 against the 0.75 default threshold, so it is flagged "Low coverage"). Argentina and the Berlin youth-vote figures from the manual check were not among the 15 ideas the model chose (the prompt asks for 8 to 15), so they are not reported; the deficit idea is reported partial. Coverage is therefore relative to the checker's own idea list and is not comparable across episodes. Accuracy also moves between runs of the same One-Pager (0.8947 in 2.1, 0.91 here, different claim sets), because the model is not deterministic.
- Choices the spec left open: the cache key does not include the output limit (so raising `max_output_tokens` after a truncation failure does not repay finished passes; the spec listed it, changed on review); a TooLong from the API on a later pass after earlier passes were paid ends the step `skipped` and those paid passes stay cached and metered, so a skipped step can have spend.
- Review patches: cache key without the output limit; idea text collapsed to one line in the coverage listing so it cannot forge numbered lines; an idea needs a title and a description; a malformed missed-idea entry no longer hides the whole check on the page; the stray comma in the library row (found by a new test); the page text now says coverage is measured against the ideas the checker found and that partly covered counts half; cache problems are logged without content; tests added for the startup wiring of thresholds and config path, library hot reload, escaped `</ideas>`, and a deterministic cache-corruption test.
- Known: a failed coverage pass fails the whole verify step and the Job (the accuracy is not shown until retry; the retry is cheap thanks to the cache); the thresholds were chosen before any real data and the first real scores suggest 0.75 will flag most episodes.

## Spec Change Log

## Review Triage Log

| Finding | Verdict | Route | Evidence |
|---|---|---|---|
| Raising `max_output_tokens` after a truncation repays finished passes | medium | patch | Key no longer includes the limit; test |
| Idea text with newlines forges extra numbered lines in the coverage prompt | medium | patch | Collapsed to one line; test |
| Idea with an empty description accepted | low | patch | Rejected |
| One malformed missed-idea entry hides the whole check on the page | medium | patch | Bad entries skipped; test |
| Library row shows a stray comma when accuracy is missing | low | patch | New test failed first, template fixed |
| Startup wiring of thresholds and config path untested; library hot reload untested | medium | patch | Two tests |
| Cache test depended on random hash digits | low | patch | Deterministic |
| Escaped `</ideas>` in the ideas pass untested | low | patch | Test |
| Cache problems invisible | low | patch | Logged without content |
| Smoke test from 2.1 assumed one call | low | patch | Updated to three calls |
| Heading and wording do not say coverage is relative to the checker's ideas | low | patch | Wording added |
| Coverage failure discards the paid accuracy result | medium | defer | Retry is Story 3.2; cache makes it cheap |
| Skipped step can have spend; coverage not comparable across runs | low | rejected | Documented in notes |
| Migration 7 duplicate column, mutable thresholds dict across threads, config read every request, no CSS for flags, `where` and covered ideas not shown, ideas and claims passes could run in parallel | low | rejected | No named harm at this scale |
| Config threshold keys missing | false | none | They exist in `app/config.py` and both TOML files |

## Verification

**Commands:**
- `uv run pytest -q` -- expected: all tests pass offline
- `RUN_NETWORK_TESTS=1 uv run pytest -q -m network` -- expected: the OpenAI smoke tests pass, including the omitted idea reported missing; under 25 cents
- Run the coverage check directly on the stored Thiel transcript and One-Pager (no writes to `data/`) -- expected: the Argentina, deficit and youth-vote ideas reported missing or partial
