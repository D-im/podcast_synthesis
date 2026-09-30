---
title: "Story 1.4: Watch a Job's progress"
type: 'feature'
created: '2026-09-30'
status: 'done'
baseline_commit: 'ad203bc00b4570f4b894917e6fb42a387c87d5bd'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-1-context.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The Episode page shows raw step states once, so a running Job looks frozen and a failed Job shows no reason.

**Approach:** Turn the Job status into a server-rendered fragment that htmx 2.x refreshes every 3 seconds. The fragment shows a plain-language status, each step with its state, and for a failed Job the failed step and its stored error message. When the Job reaches a terminal state the fragment stops asking for updates. htmx is served locally from the app, pinned at 2.0.11, with no CDN.

## Boundaries & Constraints

**Always:**
- Status wording: queued -> "Queued"; running -> by the current step: "Downloading", "Transcribing", "Summarizing", "Verifying"; done -> "Done"; failed -> "Failed at <step>" with the stored message. Between steps of a running Job, use the next pending step.
- Show every step with its state, including `skipped` with its stored reason.
- The fragment is returned by `GET /episodes/{video_id}/status` (HTML, 404 for an unknown Episode). The Episode page embeds the same fragment.
- While the Job is `queued` or `running`, the fragment carries `hx-get` to its own URL, `hx-trigger="every 3s"` and `hx-swap="outerHTML"`. When `done` or `failed` it carries none of these, so polling stops.
- The fragment includes the Episode title (or video ID until a title exists), so it updates when the title appears.
- All stored text (messages, reasons, titles) is HTML-escaped.
- Web only reads. No writes to Jobs or steps from `app/web` (AD-3).
- Serve htmx 2.0.11 as `app/web/static/htmx.min.js` mounted at `/static`, loaded by the page with a local path.

**Never:**
- No retry button, pause state, spend display, library list or recovery (later stories). No WebSocket or SSE. No htmx 4.x. No CDN or external request.
- No change to Job or step storage, the worker, or the pipeline.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Queued | Job `queued`, all steps `pending` | "Queued"; polling attributes present | N/A |
| Running | Job `running`, `transcribe` `running` | "Transcribing"; download shown `done`; polling attributes present | N/A |
| Between steps | Job `running`, no step `running`, `summarize` next `pending` | "Summarizing"; polling present | N/A |
| Done | Job `done` | "Done"; all steps listed; no `hx-get`, no `hx-trigger` | N/A |
| Done with skipped verify | `verify` `skipped` with reason | Step shows `skipped` and the reason; "Done"; no polling | N/A |
| Failed | `transcribe` `failed` with message | "Failed at transcribe" plus the message; later steps `pending`; no polling | Message escaped |
| Hostile text | Message or title contains `<script>` | Rendered as escaped text | No markup injected |
| Unknown Episode | `/episodes/nope/status` | 404 | N/A |
| Live progress | Worker running fake Job | Repeated fragment requests move from Queued through steps to Done | N/A |
| Static asset | `GET /static/htmx.min.js` | 200, content is htmx 2.0.x | N/A |

</frozen-after-approval>

## Code Map

- `app/web/app.py` -- `create_app`; `episode_page` already loads the Episode and latest Job with steps (including `message` and `retryable`). Add the status route and static mount; do not touch `/submit`.
- `app/web/templates/episode.html` -- currently prints `Job status` and a list of steps; replace with the fragment include and load htmx.
- `app/store/episodes.py` -- `get_episode` and `get_latest_job_with_steps`; reuse, do not change.
- Job states: `queued`, `running`, `done`, `failed`. Step states: `pending`, `running`, `done`, `failed`, `skipped`. Step order comes from `app.core.submit.STEP_NAMES`.
- New: `app/web/status.py` (pure function from Episode and Job dicts to a view model), `app/web/templates/_status.html`, `app/web/static/htmx.min.js`.
- Existing tests assert on the old page text (`queued`, count of `pending`); update them for the new markup.

## Tasks & Acceptance

**Execution:**
- [ ] `app/web/static/htmx.min.js` -- download htmx 2.0.11 from `https://unpkg.com/htmx.org@2.0.11/dist/htmx.min.js` and save it unmodified -- local, pinned
- [ ] `app/web/status.py` -- `describe_job(job) -> view model` with label, terminal flag, failed step and message, step rows -- status logic, no DB
- [ ] `app/web/templates/_status.html` -- the fragment per the rules above -- shared by page and poll route
- [ ] `app/web/templates/episode.html` -- include the fragment, load `/static/htmx.min.js` -- page
- [ ] `app/web/app.py` -- mount `/static`, add `GET /episodes/{video_id}/status` -- endpoint
- [ ] `tests/test_status.py` -- every matrix row; update existing assertions in `tests/test_submit.py` and `tests/test_pipeline.py` that depend on the old markup -- verification

**Acceptance Criteria:**
- Given a running Job, when the Episode page loads, then it shows the plain-language status and the fragment polls its status URL every 3 seconds.
- Given a Job that finishes, when the next poll returns, then the fragment shows "Done" and carries no polling attributes.
- Given a failed Job, when its page or fragment is viewed, then it names the failed step and shows the stored message, escaped.
- Given a fresh checkout without network access, when the page loads, then htmx comes from `/static/htmx.min.js`.
- Given the Episode page and the status route, when either is requested for an unknown Episode, then the response is 404.

## Implementation Notes

- Built as specified: vendored htmx 2.0.11 at `app/web/static/htmx.min.js`, pure `app/web/status.py` (`describe_job(episode, job)`), `_status.html` fragment, `GET /episodes/{video_id}/status`, `/static` mount, `tests/test_status.py`.
- Choices: a `paused` or other unknown Job state gets its capitalized name and no polling; a running Job with no pending or running step shows "Running".
- Review patches: status responses carry `Cache-Control: no-store`; the title-fallback test checks the heading itself; tests added for the step shown mid-run ("Transcribing" seen from inside the transcriber), step ordering, hidden messages on finished steps, and the label fallbacks.
- Verified live: submitted a link, the fragment reached Done with all four steps, htmx served from `/static`.

## Spec Change Log

## Review Triage Log

| Finding | Verdict | Route | Evidence |
|---|---|---|---|
| Title fallback assertion satisfied by other attributes | medium | patch | Assertion now checks the `<h1>` |
| `describe_job` branches and message hiding unasserted | low | patch | Pure unit tests added (reviewer suggested defer; cheap) |
| "Live progress" only sampled Queued and Done | medium | patch | Test now reads the fragment during the transcribe step |
| Status response could be cached | low | patch | Direct one-line header |
| Poll failure shows no stale indicator; 404 poll keeps polling | low | rejected | Episodes are never deleted; reload recovers; fix adds JS |
| Unknown or None Job state, missing steps key | low | rejected | State is NOT NULL and set only by the worker |
| Failed job with empty message; multiple failed steps | low | rejected | The pipeline always stores a message and stops at the first failure |
| `<title>` uses video ID; aria-live; visual indicators; urlencode of video ID | low | rejected | No named harm; ID is validated |
| Vendored htmx version test is brittle | low | rejected | Pinning the version is the point |
| Substring-count assertions, ternary readability, unused import claims | low | rejected | Style only |

## Verification

**Commands:**
- `uv run pytest -q` -- expected: all tests pass
- `head -c 200 app/web/static/htmx.min.js` -- expected: htmx 2.0.11 header or banner
- `uv run podcast-synthesis`, submit a watch URL, watch the page without reloading -- expected: status moves through steps to Done on its own, then stops requesting `/status`
