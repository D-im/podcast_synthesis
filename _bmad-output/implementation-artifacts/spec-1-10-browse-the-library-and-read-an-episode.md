---
title: 'Story 1.10: Browse the library and read an episode'
type: 'feature'
created: '2026-09-30'
status: 'done'
baseline_commit: '81cf4d04dc388db63af894f0d839edf7c3bdbf96'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-1-context.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** Past Episodes can only be reached by typing their URL. There is no list, no search, and the Episode page has no link to the audio.

**Approach:** The home page keeps the submit form and adds a library of past Episodes, newest first, each with title, date and status. A search box filters by plain text over titles and the latest One-Pager text. The Episode page gains a link and player for the stored audio. No embeddings and no new tables.

## Boundaries & Constraints

**Always:**
- Library on `/` below the submit form (also when the form shows an error). Each row: title (video ID until a title exists), local date and time of submission, status label (the same wording as the Episode status: Queued, Downloading, ..., Done, Failed at <step>), linking to the Episode page. Newest first. Processing and failed Episodes are listed like the rest.
- The default list shows the latest 100 Episodes. A search scans all Episodes and shows up to 200 matches, with a note when results were cut off.
- Search is `GET /?q=...`. The query is trimmed and cut at 200 characters. Empty or blank shows the normal list. Words split on whitespace; an Episode matches when every word appears, case-insensitively, somewhere in its title or its latest One-Pager text. Characters such as `%`, `_`, quotes and `<` are plain text. Only the latest One-Pager version is searched; transcripts are not. A matching row shows a short escaped snippet of the first match in the One-Pager. An unreadable One-Pager file does not break search: that Episode can still match by title.
- Empty states: "No Episodes yet." and "No Episodes match '<query>'." (query escaped).
- All stored text and the echoed query are HTML-escaped.
- Episode page: keep the status fragment as it is. Add the submission date, a link to the source video, and, once the audio file exists, an `<audio controls preload="none">` player and a download link served by `GET /episodes/{video_id}/audio` (`audio/mp4`, 404 if the file is missing, video ID validated as for other artifact routes). The Transcript link stays as is.
- Verdicts and Fidelity Scores do not exist yet. The library row and Episode view model carry `verdict` and `fidelity` fields that are empty now and rendered only when set, so Epic 2 can fill them. Do not create tables for them.
- Web reads only. Episode, job and step data come through `app/store`; search reads One-Pager files through `app/store/artifacts`.

**Never:**
- No pagination controls, delete or edit actions, tags, semantic search, or transcript search. No new tables or migrations. No JavaScript beyond the existing htmx. No change to the worker or pipeline.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| List | Several Episodes in mixed states | Newest first; title or ID, local date, status label; processing and failed shown | N/A |
| Empty library | No Episodes | "No Episodes yet." | N/A |
| Search by title | `q=thiel` (any case) | Episodes whose title contains it | N/A |
| Search One-Pager | `q=` word in a latest One-Pager | Episode matches; snippet shown | N/A |
| Several words | `q=sleep caffeine` | Only Episodes containing both words | N/A |
| Older version | Word only in v1 when v2 is latest | No match | N/A |
| No match | `q=zzz` | "No Episodes match 'zzz'." | N/A |
| Blank or whitespace | `q=` or `q=%20%20` | Normal list | N/A |
| Hostile or special | `q=%`, `q=_`, `q=<script>` | Treated as text; output escaped; no error | No injection |
| Very long | 5000-character `q` | Cut to 200; works | N/A |
| Unreadable One-Pager | Corrupt file | Search and list still work; matches by title only | No 500 |
| Cut-off | More than 200 matches | First 200 shown with a note | N/A |
| Episode page | Finished Episode | One-Pager, spend, Transcript link, audio player and link, source link, date | N/A |
| Audio route | File present / absent | 200 `audio/mp4` / 404; bad ID 404 | N/A |
| Early Episode | Audio not downloaded yet | No audio player or link | N/A |
| Error page | Bad link submitted | Form error and the library both shown | N/A |

</frozen-after-approval>

## Code Map

- `app/web/app.py` -- `create_app`, `home_response` (used by `/` and the submit error paths), `load()` (builds the Episode view model with One-Pager and spend), transcript route as the pattern for artifact routes. `app/web/status.py` -- `describe_job` gives the status label; `format_timestamp`.
- `app/web/templates/base.html`, `home.html`, `episode.html`, `_status.html` -- extend; the fragment already shows the One-Pager, spend and the Transcript link.
- `app/store/episodes.py` -- `get_episode`, `get_latest_job_with_steps`, `latest_one_pager_version`; add a list query. `app/store/artifacts.py` -- `one_pager_path`, `read_json`, `AUDIO`, `episode_dir` (validates the ID).
- Dates are stored as UTC ISO strings (`episodes.created_at`); display in local time.
- New: `app/web/library.py` (list and search logic over store calls, no HTTP), `app/web/templates/_library.html`, `tests/test_library.py`.

## Tasks & Acceptance

**Execution:**
- [ ] `app/store/episodes.py` -- query for Episodes with their latest Job state and steps, newest first, with a limit -- library data
- [ ] `app/web/library.py` -- list rows, query parsing, AND-of-words matching over title and latest One-Pager text, snippets, limits -- library logic
- [ ] `app/web/templates/_library.html`, `home.html`, `base.html` -- library with search box and empty states, shown on every `/` render -- UI
- [ ] `app/web/app.py`, `episode.html`, `_status.html` -- `/?q=` handling, audio route, date, source link, audio player and link -- Episode view
- [ ] `tests/test_library.py` -- every matrix row using temp data and fake Episodes; update existing tests that assert on the old home page -- verification

**Acceptance Criteria:**
- Given Episodes in different states, when the home page loads, then each appears newest first with title, local date and status, processing and failed included.
- Given a search word present in a title or the latest One-Pager, when searched in any case, then that Episode is found and others are not.
- Given hostile or special characters in the query, when searched, then no error occurs and output is escaped.
- Given a finished Episode, when its page loads, then the One-Pager, spend, Transcript link and audio player are present; given audio not yet downloaded, no audio control shows.
- Given an unreadable One-Pager file, when the library or search runs, then the page still loads.

## Implementation Notes

- Built as specified: `episodes.list_episodes`, `app/web/library.py` (list, query parsing, AND-of-words search, snippets), `_library.html` on the home page, audio route and player, submission date and source link on the Episode page, `verdict` and `fidelity` slots left empty for Epic 2, `tests/test_library.py`.
- Choices: if the library cannot be read the home page shows "The library is unavailable right now." and still serves the form; dates display in the server's local time; "latest One-Pager" is the highest version row, so a legacy `one_pager.json` is not searched.
- Verified live with the real app on the supplied link: the library lists the real title and status, `/?q=thiel` narrows to it, and the audio route serves `206` for a `Range` request (so the player can seek).
- Review patches: search matching and snippets now use one case-insensitive regex on the original text, so characters whose case folding changes length (German sharp s, dotted capital I) no longer lose the snippet; the library degrades instead of a 500 when a row is malformed; the audio route serves inline (the link's `download` attribute saves it) and answers 404 instead of a 500 if the file vanishes; a non-http source URL is shown as text, not a link; tests added for the degraded library, retried Episodes (newest job wins, no-job Episodes listed, `limit`), a finished Episode page (One-Pager, spend, Transcript link, audio), range requests, and the snippet fix.

## Spec Change Log

## Review Triage Log

| Finding | Verdict | Route | Evidence |
|---|---|---|---|
| Snippet lost or misplaced when case folding changes text length | medium | patch | Regex on the original text; test with sharp s and dotted capital I |
| Library failure path untested; narrow except tuple | medium | patch | Test added; except widened to malformed-row errors |
| Newest-job selection in `list_episodes` untested | medium | patch | Retried Episode, no-job Episode and limit tests |
| Finished Episode page not tested as a whole | medium | patch | One test checks One-Pager, spend, transcript link and audio |
| Audio served as an attachment; directory or vanished file gives a 500 | medium | patch | Inline disposition; 404 guard; range test |
| Non-http source URL rendered as a link | low | patch | Template guard |
| Search reads every One-Pager file per query; steps query unscoped | low | rejected | Fine at personal scale; accepted in the spec |
| Audio player shown while download incomplete | low | rejected | Files appear only by atomic rename |
| Snippet shows only the earliest hit; unreadable One-Pager not flagged in search | low | rejected | Spec behaviour |
| Hard-coded `audio/mp4`; no pagination or clear-search link; count header | low | rejected | Out of scope by the spec |

## Verification

**Commands:**
- `uv run pytest -q` -- expected: all tests pass
- `uv run podcast-synthesis`, submit two links, open `/?q=<a title word>` -- expected: the library lists both; the search narrows to the matching one
- Open a finished Episode -- expected: audio plays in the page and downloads from the link
