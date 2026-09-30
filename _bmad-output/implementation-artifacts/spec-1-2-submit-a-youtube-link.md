---
title: 'Story 1.2: Submit a YouTube link'
type: 'feature'
created: '2026-09-30'
status: 'done'
baseline_commit: '75ced74b71ce16bc8afd6a8ad18f955d14686fd8'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-1-context.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** There is no way to hand the app a video. Every later story needs an Episode and a queued Job with persisted steps to work on.

**Approach:** Add a submit form on the home page. A valid YouTube URL creates an Episode keyed by its video ID, plus one queued Job with its four steps stored as `pending`, then shows the Episode page. A URL for an existing video shows the existing Episode. An invalid URL is rejected with no rows written. This story also creates the first tables, a simple versioned migration mechanism, and sets `foreign_keys=ON` and a busy timeout on every connection (deferred from Story 1.1).

## Boundaries & Constraints

**Always:**
- Episode identity is the 11-character video ID. Every URL form of one video resolves to one Episode (AD-9).
- Accept `youtube.com` (also `www.`, `m.`, `music.`) `/watch?v=`, `/shorts/`, `/embed/`, `/live/`, and `youtu.be/<id>`, over http or https. Ignore extra query parameters.
- Steps are exactly `download`, `transcribe`, `summarize`, `verify`, stored in that order, all `pending`; Job state is `queued` (AD-1).
- Layering: `app/core` parses and orchestrates, `app/store` holds all SQL, `app/web` calls core. Store does not import core. Core step names are passed to store.
- Episode and Job creation happen in one transaction, so a failure leaves nothing behind.
- Only tables `episodes`, `jobs` and `steps` exist after this story. Schema changes go through numbered migrations tracked with `PRAGMA user_version`, applied by `db.bootstrap`.
- Every connection sets `foreign_keys=ON` and `busy_timeout=5000`, and WAL stays verified.

**Never:**
- No worker, ports, fakes, downloading, metadata lookup, spend or library list (later stories).
- No network call at submission. Title and duration stay NULL until Story 1.6.
- Do not accept hosts that merely contain `youtube.com` (for example `youtube.com.evil.com`), playlist-only URLs, or non-http(s) schemes.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| New video | Valid watch URL, no Episode | Episode created with video ID, Job `queued`, 4 steps `pending`, 303 to `/episodes/<id>`, page shows status | N/A |
| Other URL forms | `youtu.be/<id>`, `/shorts/<id>`, `/embed/<id>`, `m.youtube.com/watch?v=<id>&t=30s` | Same video ID extracted | N/A |
| Duplicate | Same video, any URL form | 303 to the existing Episode; still one Episode and one Job | N/A |
| Not YouTube | `https://example.com/x`, `youtube.com.evil.com/watch?v=<id>`, `ftp://...`, plain text, empty | 400 with message on home page; zero rows in all tables | No spend, no partial state |
| Bad video ID | `watch?v=short` or playlist-only link | Rejected as above | Same |
| Mid-create failure | DB error while writing steps | Nothing stored (rolled back) | Error surfaced, no partial Episode |
| Unknown Episode | `/episodes/doesnotexist` | 404 | N/A |
| Fresh vs existing DB | Start on empty DB, then restart | Migrations apply once; `user_version` set; data kept | N/A |

</frozen-after-approval>

## Code Map

- `app/store/db.py` -- `connect()` and `bootstrap()`; add per-connection PRAGMAs and migration runner here. `connect()` already verifies WAL.
- `app/web/app.py` -- `create_app(warnings)`; extend to take `data_dir`, add `POST /submit` and `GET /episodes/{video_id}`, open a connection per request. Existing tests call `create_app([])`.
- `app/web/templates/home.html` -- add the form and an error slot; keep the warnings block.
- `app/main.py` -- passes `create_app(warnings)`; pass `data_dir` too.
- `tests/test_startup.py` -- `test_fresh_start_creates_wal_db` asserts zero tables; update to the three expected tables.
- New: `app/core/youtube.py`, `app/core/submit.py`, `app/store/migrations.py`, `app/store/episodes.py`, `app/web/templates/episode.html`.
- Story 1.1 left `foreign_keys` and `busy_timeout` undecided; decided here. Do not change config loading.

## Tasks & Acceptance

**Execution:**
- [ ] `app/core/youtube.py` -- `parse_video_id(url) -> str | None` per the rules above -- single place for URL forms
- [ ] `app/store/migrations.py` -- numbered migration list; migration 1 creates `episodes(video_id PK, url, title NULL, duration_seconds NULL, created_at)`, `jobs(id PK, video_id FK, state, created_at, updated_at)`, `steps(job_id FK, name, ordinal, state, PK(job_id, name))` -- first tables, versioned
- [ ] `app/store/db.py` -- PRAGMAs `foreign_keys=ON`, `busy_timeout=5000` on every connection; `bootstrap` applies pending migrations -- the deferred decision
- [ ] `app/store/episodes.py` -- `create_episode_with_job(conn, video_id, url, step_names)` in one transaction returning `(episode, created)`; `get_episode`, `get_latest_job_with_steps` -- all SQL for this story
- [ ] `app/core/submit.py` -- `STEP_NAMES`, `submit(conn, url)` returning a result or a rejection reason -- core orchestration
- [ ] `app/web/app.py` and templates -- form on home, `POST /submit` (303 or 400), `GET /episodes/{video_id}` (Job state and step states, 404 if unknown) -- UI
- [ ] `app/main.py` -- pass `data_dir` to `create_app` -- wiring
- [ ] `tests/` -- cover every matrix row, PRAGMAs on a fresh connection, and migrations idempotent on restart; update the 1.1 table-count test

**Acceptance Criteria:**
- Given a valid YouTube URL in any accepted form, when submitted, then one Episode, one `queued` Job and four `pending` steps in order exist, and the Episode page shows them.
- Given a duplicate video in a different URL form, when submitted, then no new rows are created and the existing Episode is shown.
- Given an invalid URL, when submitted, then it is rejected with a message and every table is still empty.
- Given a new connection, when it opens, then `foreign_keys` is 1, `busy_timeout` is 5000 and `journal_mode` is `wal`.
- Given only this story is applied, when the DB is inspected, then the only tables are `episodes`, `jobs` and `steps`.

## Implementation Notes

- Built as specified: `app/core/youtube.py`, `app/core/submit.py`, `app/store/{migrations,episodes}.py`, per-connection PRAGMAs in `app/store/db.py`, `POST /submit` and `GET /episodes/{video_id}`, `episode.html`, `tests/test_submit.py`.
- Form body is parsed with `urllib.parse.parse_qs` because FastAPI `Form` would need the `python-multipart` dependency.
- Review patches: submit runs in a worker thread with its own connection so a locked DB cannot stall the event loop; DB errors return 503 with a retry message and leave no rows; the stored `episodes.url` is the canonical `https://www.youtube.com/watch?v=<id>` (no tracking parameters); migrations refuse a newer schema version and guard rollback; added tests for concurrent duplicate submits, failed migration rollback, newer schema, and the 503 path.
- Episode page has no auto-refresh yet (Story 1.4).

## Spec Change Log

## Review Triage Log

| Finding | Verdict | Route | Evidence |
|---|---|---|---|
| Async handler runs blocking SQLite, stalling the event loop up to the busy timeout | medium | patch | `request.body()` handler called `core_submit.submit` directly; now `run_in_threadpool` |
| DB errors on submit return a bare 500 | medium | patch | No handler; matrix says error surfaced; now 503 page, no rows |
| Duplicate-submit race guard has no concurrent test | medium | patch | All tests were serial; threaded test added |
| Stored URL is raw user input with tracking parameters | medium | patch | Later download step reads `episodes.url`; now canonical URL |
| Newer `user_version` silently accepted; rollback not guarded | medium | patch | Both real in `migrate`; fixed |
| `assert` stripped under `-O` | low | patch | Replaced with RuntimeError |
| Failed-migration path untested | medium | patch | Test added (reviewer suggested defer; cheap to close now) |
| Unbounded request body | low | rejected | Local single-user app, fix adds branching |
| Credentials or port in URL accepted | low | rejected | Only the canonical URL is stored now, so nothing is persisted from them |
| Web imports store directly, bypassing core | false | none | Spine's dependency diagram allows Web to Store |
| Episode and job read in two statements | low | rejected | Display only |
| CHECK constraints, indexes, UNIQUE ordinal, Row factory, aria/autofocus | low | rejected | No named harm |
| Episode page never refreshes | false | none | Story 1.4 |
| `_run_with` app points at real data dir | low | rejected | No test submits through it |
| Multipart form undocumented | low | rejected | Noted in Implementation Notes |

## Verification

**Commands:**
- `uv run pytest -q` -- expected: all tests pass
- `uv run podcast-synthesis`, then submit a watch URL at `http://127.0.0.1:8765/` -- expected: redirect to `/episodes/<id>` showing Job `queued` and four `pending` steps; resubmitting shows the same Episode
- `sqlite3 data/podcast_synthesis.db ".tables" "PRAGMA user_version;"` -- expected: `episodes jobs steps`, version 1
