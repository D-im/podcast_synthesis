---
title: 'Story 4.1: Name the speakers'
type: 'feature'
created: '2026-10-01'
status: 'done'
baseline_commit: '2a6e9f8'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-1-context.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The Transcript labels speakers "A" and "B", so the One-Pager says "Speaker A argues ..." and the reader has to work out who that is.

**Approach:** On the Episode page, Diimrem types a name for each speaker label found in the stored Transcript. Names are stored per Episode; the stored Transcript file is never changed. The Transcript page shows the names. Summarize and verify receive the Transcript with the names in place of the labels, so the next One-Pager attributes claims by name. Existing Episodes use Regenerate to get a new version with names; earlier versions keep their text. Saving names never queues anything and never spends money.

## Boundaries & Constraints

**Always:**
- Migration 12 adds `speaker_names(video_id, label, name, PRIMARY KEY (video_id, label))` with a foreign key to `episodes`, and `one_pager_versions.speaker_names TEXT` (nullable; JSON object label to name as used for that version, or NULL when no names were used or the version is older than this story).
- `app/core/speakers.py`: `labels(transcript)` returns the distinct non-empty speaker labels in first-appearance order; `validate(raw)` trims each name, an empty name means "no name" (that label is cleared), at most 60 characters, no control characters or line breaks, a label not in the Transcript is rejected, and returns the clean mapping or a message. Two labels may share a name (diarization sometimes splits one person into two labels). `apply(transcript, names)` returns a new `Transcript` with each mapped label replaced by its name and every other segment unchanged; it never mutates or writes the stored file.
- Store functions in `app/store/episodes.py`: `get_speaker_names(conn, video_id)`, and `set_speaker_names(conn, video_id, mapping)` which replaces the Episode's names in one transaction (labels left out or cleared are removed). `add_one_pager_version` gains an optional `speaker_names` argument stored as sorted JSON.
- Pipeline: `run_job` reads the names once per run of `summarize` and `verify` and passes `speakers.apply(transcript, names)` to the adapters. Adapters, prompts, quote matching and caches are unchanged; caches key on the content sent, so they refresh when names change. The names used are stored on the new One-Pager version. `verify` uses the names stored on the version it checks, so a check always matches its One-Pager.
- Web: `POST /episodes/{video_id}/speakers` with one field `name_<label>` per label shown. Success redirects 303 to the Episode. An unknown Episode is 404; a Transcript that is missing or unreadable is 409 with a message; invalid input is 400 with the Episode page, a message and the typed values kept. A GET never changes anything.
- The Speakers section on the Episode page lists each label with a name field and the saved name. It appears only when the stored Transcript has at least one speaker label, and lives outside the polled status fragment, like the Verdict form, so the live refresh cannot wipe typing. It says that names apply to the next summarize, verify or Regenerate, and that earlier One-Pager versions keep their text.
- The Transcript page shows the saved names in place of labels. Names are HTML-escaped everywhere.
- Version history (Story 2.4): the change summary also reports "speaker names changed" when the names stored on a version differ from the previous version's (a NULL and an empty set count as the same; a version with NULL beside one with names counts as changed). It keeps the existing wording otherwise. A version with no stored names data does not claim a change.
- The Regenerate confirmation page states that the current speaker names will be used when any are saved.
- Editing names never queues a Job, never calls a vendor and never changes the ledger, One-Pager versions, scores or Verdicts.

**Never:**
- No guessing or suggesting names (no model call, no AssemblyAI Speaker Identification), no editing the stored Transcript, no renaming inside old One-Pager versions, no remembering names across Episodes, no automatic regeneration, no change to the estimator or the Daily Cap logic, no change to diarization settings.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Save names | Labels A and B, names "Host" and "Peter Thiel" | Stored; redirect to Episode; Transcript page shows the names | N/A |
| Edit | Change B's name | Replaced; old name gone | N/A |
| Clear | Empty name for A | A's name removed; plain label shown again | N/A |
| Shared name | A and B both "Peter Thiel" | Accepted | N/A |
| Too long | 61 characters | Nothing stored; message; typed values kept | 400 |
| Bad characters | Line break or control character | Nothing stored; message | 400 |
| Unknown label | Field for a label not in the Transcript | Nothing stored | 400 |
| No speakers | Transcript without labels | No Speakers section; POST stores nothing | 400 |
| No Transcript | File missing or unreadable | No section; POST stores nothing | 409 |
| Unknown Episode | Bad video ID | 404 | N/A |
| Stored file | Names saved | `transcript.json` byte-identical | N/A |
| Summarize | Names saved, Job runs | Summarizer receives named segments; version stores the names | N/A |
| Verify | Same Job | Verifier receives named segments; quote matching still finds quotes | N/A |
| Regenerate | Names changed since last version | New version built with names; history says "speaker names changed" | N/A |
| Old version | Version without stored names | Opens as before; no change claimed | N/A |
| No names | Nothing saved | Pipeline output identical to today | N/A |
| Live refresh | Job polling while typing | Form not replaced | N/A |
| Hostile text | Name with markup | Escaped | N/A |
| Migration | Database at schema 11 | Migrates to 12, data intact | N/A |
| Cost | Saving names | No Job, no spend | N/A |

</frozen-after-approval>

## Code Map

- `app/ports/__init__.py` -- `Segment`, `Transcript`. `app/core/pipeline.py` -- `run_step` for `summarize` and `verify`, where the Transcript is read. `app/store/episodes.py` -- `add_one_pager_version`, `list_one_pager_versions`. `app/store/migrations.py` -- add migration 12.
- `app/web/app.py` -- `load()`, `transcript_page`, `version_page`, `regenerate_page`; `app/web/templates/episode.html`, `transcript.html`, `regenerate.html`; `app/core/regenerate.py` -- `change_summary`, `history`.
- New: `app/core/speakers.py`, `app/web/templates/_speakers.html`, `tests/test_speakers.py`. Existing tests asserting schema version 11 move to 12, and the startup table list gains `speaker_names`.

## Tasks & Acceptance

**Execution:**
- [ ] `app/store/migrations.py`, `app/store/episodes.py` -- table, version column, get and set -- persistence
- [ ] `app/core/speakers.py` -- labels, validate, apply -- rules
- [ ] `app/core/pipeline.py` -- apply names for summarize and verify, store them on the version -- pipeline
- [ ] `app/web/app.py`, templates, `app/core/regenerate.py` -- Speakers form and POST, Transcript page, Regenerate note, history summary -- UI
- [ ] `tests/test_speakers.py` -- every matrix row with fakes -- verification

**Acceptance Criteria:**
- Given an Episode whose Transcript has speaker labels, when I open it, then I can enter a name for each label.
- Given saved names, when I open the Transcript, then it shows the names, and the stored file is unchanged.
- Given saved names, when summarize and verify run, then they receive the names in place of the labels, and quote matching still works.
- Given names changed after a One-Pager exists, when I Regenerate, then the new version uses the names and the history says the names changed; the old version is untouched.
- Given a name that is too long, has a line break, or belongs to an unknown label, then it is rejected with a message and nothing is stored.
- Given saved names, then nothing is queued and nothing is spent.

## Verification

**Commands:**
- `uv run pytest -q` -- expected: all tests pass offline

## Implementation Notes

- Built as specified: migration 12 (`speaker_names`, `one_pager_versions.speaker_names`), `app/core/speakers.py`, `episodes.get_speaker_names` / `set_speaker_names`, names applied in `pipeline.run_job` for summarize and verify (verify uses the names stored on the version it checks), `POST /episodes/{id}/speakers`, `_speakers.html` outside the polled fragment, names on the Transcript page, Regenerate note, "speaker names changed" in the version history, `tests/test_speakers.py` (31 tests).
- Judgment calls: summarize ignores saved names whose label is no longer in the Transcript. The names recorded on a version are exactly those applied. A bad-input response re-shows the typed values for the labels the form has. Schema-version assertions moved from 11 to 12 and the startup table list gained `speaker_names`.
- Not done: no real run with a real diarized Transcript and the real summarizer; no code-review pass; no AssemblyAI Speaker Identification (out of scope).
