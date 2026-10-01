---
title: 'Story 2.5: Record a Verdict'
type: 'feature'
created: '2026-10-01'
status: 'done'
baseline_commit: 'b216759'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-1-context.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Diimrem can read a One-Pager and see an automated score, but cannot record whether the episode was worth his time, so nothing builds up about what earns it.

**Approach:** The Episode page gets a Verdict form (worth it / not worth it, plus a required reason). A Verdict is stored with the One-Pager version shown on the form, the rating, the reason and the time. Verdicts are never edited or deleted; the newest is the current one and older ones are listed below it. The library shows each Episode's current Verdict. Each Verdict joins to its One-Pager version, Fidelity Score and prompt hashes by `(video_id, version)`.

## Boundaries & Constraints

**Always:**
- Migration 8 adds `verdicts(id INTEGER PRIMARY KEY AUTOINCREMENT, video_id, version, rating, reason, created_at)`. `rating` is checked to be `worth_it` or `not_worth_it`. `(video_id, version)` has a foreign key to `one_pager_versions`. Index on `(video_id, id)`. Existing tables are unchanged.
- Store functions in `app/store/episodes.py`: `add_verdict` (one transaction; fails cleanly if the version does not exist), `list_verdicts(video_id)` newest first, and `list_episodes` also returns the current Verdict's rating and version.
- `app/core/verdict.py` validates: rating must be one of the two values; reason is trimmed and must not be empty; reason is at most 5000 characters. It returns the clean values or a rejection message.
- `POST /episodes/{video_id}/verdict` with fields `rating`, `reason`, `version` (hidden, the version shown on the form). The stored version is the posted one, which must exist for that Episode. If the page showed v1 and v2 has since appeared, the Verdict is stored on v1. Success redirects 303 to the Episode. Rejection returns 400 with the Episode page, a message and the typed reason and rating kept. Unknown Episode is 404. A malformed or unknown version is 400 with a message and nothing stored.
- The form shows only when the Episode has a One-Pager version, and it is for the latest version. It says which version it rates.
- Episode page: the current Verdict (rating, reason, version rated, local date and time). When its version is not the latest, it says so plainly ("rated v1; the latest is v2"). Older Verdicts are listed below, newest first, each with the same fields. All stored text is HTML-escaped and keeps line breaks.
- Library row shows the current Verdict as "Worth it" or "Not worth it", with the version rated when it is not the latest.
- Spend, Jobs, One-Pagers, scores and regeneration are untouched. The existing "Verdict" placeholders in `app/web/app.py` and `app/web/library.py` are replaced.

**Never:**
- No editing or deleting a Verdict, no rating from an earlier version's page, no numeric scale or tags, no export feature, no effect of a Verdict on scores, flags or thresholds, no change to Jobs or the pipeline.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Record | Episode with One-Pager v1, rating and reason | Verdict stored with version 1, rating, reason, time; redirect to Episode | N/A |
| No reason | Empty or whitespace reason | Nothing stored; message; typed rating kept | 400 |
| Bad rating | Missing or unknown rating | Nothing stored; message | 400 |
| Too long | Reason over 5000 characters | Nothing stored; message; text kept | 400 |
| Second Verdict | Episode already has one | Both kept; latest shown as current, older listed | N/A |
| Newer version | Verdict on v1, v2 exists | Current Verdict says it rated v1 and the latest is v2 | N/A |
| Stale form | Form showed v1, v2 appeared before submit | Verdict stored on v1 | N/A |
| Bad version | Missing, non-numeric, or nonexistent version | Nothing stored; message | 400 |
| No One-Pager | Episode without a version | No form; POST stores nothing | 400 |
| Unknown Episode | Bad video ID | 404 | N/A |
| Library | Episodes with and without Verdicts | Latest Verdict per Episode, none shown when absent | N/A |
| Join | Verdict in database | Joins to its version row, Fidelity Score and prompt hashes | N/A |
| Hostile text | Reason with markup | Escaped | N/A |
| Migration | Database at schema 7 | Migrates to 8 with existing data intact | N/A |

</frozen-after-approval>

## Code Map

- `app/store/migrations.py` -- add migration 8. `app/store/episodes.py` -- `latest_one_pager_version`, `list_one_pager_versions`, `list_episodes`; add verdict functions.
- `app/web/app.py` -- `load()` (Episode view model, `status["verdict"]` placeholder), `episode_page`; `app/web/templates/_status.html`; `app/web/library.py` -- `_row` placeholder, `local_datetime`; `app/web/templates/_library.html`.
- New: `app/core/verdict.py`, `tests/test_verdict.py`.

## Tasks & Acceptance

**Execution:**
- [ ] `app/store/migrations.py`, `app/store/episodes.py` -- verdicts table, add, list, library data -- persistence
- [ ] `app/core/verdict.py` -- validation -- rules
- [ ] `app/web/app.py`, templates, `app/web/library.py` -- form, POST, current and older Verdicts, library display -- UI
- [ ] `tests/test_verdict.py` -- every matrix row -- verification

**Acceptance Criteria:**
- Given an Episode with a One-Pager, when I open it, then I can record a rating and a reason.
- Given a submitted Verdict, then it stores the version shown, rating, reason and time, and a Verdict without a reason is rejected with a message.
- Given a second Verdict, then both are kept and the latest is current.
- Given the library, then each Episode shows its latest Verdict, and older ones are on the Episode page.
- Given a Verdict on v1 and a newer v2, then the Verdict clearly says it rated v1.
- Given Verdicts in the database, then each joins to its version, Fidelity Score and prompt hashes.

## Verification

**Commands:**
- `uv run pytest -q` -- expected: all tests pass offline

## Implementation Notes

- Built as specified: migration 8 (`verdicts`), `episodes.add_verdict` / `list_verdicts`, `app/core/verdict.py`, `POST /episodes/{id}/verdict`, Verdict section and library display, `tests/test_verdict.py`.
- Judgment calls: the Verdict section is in its own template (`_verdict.html`) outside the polled status fragment, so the 3-second refresh cannot wipe a reason being typed. Schema-version assertions in older tests moved from 7 to 8, and the startup table list gained `verdicts` and `sqlite_sequence`.
- Not done: no code-review pass yet; story is in `review`.
