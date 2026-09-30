---
title: 'Story 1.6: Download audio'
type: 'feature'
created: '2026-09-30'
status: 'done'
baseline_commit: '3aa82d5446b6eb8596cce0fc7afab1e6f14f136b'
route: 'dispatch'
review_loop_iteration: 0
context:
  - '{project-root}/_bmad-output/implementation-artifacts/epic-1-context.md'
---

<frozen-after-approval reason="human-owned intent — do not modify unless human renegotiates">

## Intent

**Problem:** The `download` step only has a fake. The pipeline needs real audio, and the video's title and duration, from YouTube. Later cost estimates depend on the duration.

**Approach:** Add a yt-dlp adapter that implements the `Downloader` port. It fetches audio only, converts it to m4a with ffmpeg, leaves the file at the path the pipeline gave it, and returns the title and duration. Expected failures become `StepError`s with readable reasons and the right `retryable` flag. `downloader = "yt-dlp"` becomes the default in `config.toml`. Tests never touch the network, with one opt-in real-network smoke test. A small Story 1.2 fix rides along: the URL parser also accepts links pasted without a scheme.

## Boundaries & Constraints

**Always:**
- Pin `yt-dlp[default]` to the verified current release (2026.8.19 as researched; confirm it resolves) so the `yt-dlp-ejs` solver scripts are installed. Use yt-dlp's Python API.
- Configure yt-dlp with the JS runtime named in `config.toml` (`[ytdlp] js_runtime = "node"`, allowed `node` or `deno`). Check the installed yt-dlp's documentation for the exact option shape instead of assuming it.
- Download audio only (best audio, extract to m4a), `noplaylist`, quiet, no progress output, a socket timeout and limited retries. Never read the audio into Python memory.
- Work in a scratch folder beside `dest` (`.ytdlp-work`), then move the finished m4a onto `dest` with `os.replace`. Remove the scratch folder on success and on any failure, including exceptions and interrupts. Nothing else may be left in the episode folder.
- Return `DownloadResult(title, duration_seconds)`: title trimmed to 300 characters, duration rounded to whole seconds.
- Error mapping to `StepError(message, retryable)`, messages short and readable, never containing secrets:
  - private, removed, unavailable, members-only, age-restricted, region-blocked, or a live or upcoming stream: `retryable=False`, with the reason.
  - network errors, timeouts, HTTP 429 or 5xx, and bot or sign-in checks: `retryable=True`, with a hint to update yt-dlp, try again later, or use browser cookies.
  - ffmpeg not found: `retryable=True`, "ffmpeg not found, install it and retry".
  - yt-dlp finished but produced no file, or returned no duration: a clear `StepError`.
  - any other yt-dlp download error: `retryable=True`, generic message plus yt-dlp's short error text.
- `app/core/youtube.py` `parse_video_id` also accepts a link with no scheme (`youtube.com/watch?v=...`, `www.youtube.com/...`, `youtu.be/...`), treating it as https. Other schemes, lookalike hosts, userinfo and playlist-only links stay rejected. A pasted link with tracking parameters such as `&source_ve_path=...&embeds_referring_euri=...` resolves to its video ID.
- A download is free: the adapter records nothing through the meter.
- `build_adapters` accepts `downloader = "yt-dlp"` (and still `fake`). `config.toml` defaults to `yt-dlp` for the downloader and keeps the other roles `fake`.
- Tests never use the network. A `conftest.py` autouse fixture blocks socket connections unless a test is marked `network`. Existing tests that run `main.run()` or build adapters from `config.toml` must use `fake` providers so they stay offline.

**Never:**
- No transcription, summarizing or metadata prefetch at submission. No cookies handling code (only the hint), no proxy support, no playlist handling.
- No change to the pipeline, ports or ledger.

## I/O & Edge-Case Matrix

| Scenario | Input / State | Expected Output / Behavior | Error Handling |
|----------|--------------|---------------------------|----------------|
| Success | Valid video | m4a at `dest`; title and duration returned; scratch folder gone; Episode title and duration saved by the pipeline | N/A |
| Private or removed | yt-dlp "Video unavailable" or "Private video" | `StepError`, `retryable=False`, reason | Step `failed` |
| Live or upcoming | `is_live` or `live_status` upcoming | `StepError`, `retryable=False`, "live stream" | Step `failed` |
| Bot or sign-in check | yt-dlp "Sign in to confirm you're not a bot" | `retryable=True` with update-or-cookies hint | Step `failed` |
| Network or rate limit | Timeout, 429, 5xx | `retryable=True` | Step `failed` |
| ffmpeg missing | ffmpeg not on PATH | `retryable=True`, names ffmpeg | Step `failed` |
| No file produced | yt-dlp returns but nothing in scratch | Clear `StepError` | Scratch removed |
| No duration | Metadata lacks `duration` | `StepError`, `retryable=False` | Scratch removed |
| Mid-download crash | Exception after partial files | Scratch folder removed; no `dest`, no stray files | Exception mapped as above |
| Provider selection | `downloader = "yt-dlp"` | Adapter built; `fake` still works; unknown name raises | Startup config error |
| Scheme-less link | `youtube.com/watch?v=B7yl7fEHeKM&source_ve_path=OTY3MTQ&embeds_referring_euri=https%3A%2F%2Fmarginalrevolution.com%2F` | Video ID `B7yl7fEHeKM` | N/A |
| Still rejected | `ftp://youtube.com/watch?v=<id>`, `youtube.com.evil.com/watch?v=<id>`, `hello` | Rejected | N/A |
| Offline tests | Any non-`network` test | Socket connect is blocked | Test fails if it tries |
| Real smoke | `RUN_NETWORK_TESTS=1`, short public video | Real m4a under `dest`, real title and duration | Skipped by default |

</frozen-after-approval>

## Code Map

- `app/ports/__init__.py` -- `Downloader.download(video_id, url, dest, meter)`, `DownloadResult`, `StepError`. Keep as is.
- `app/core/pipeline.py` -- runs `download` inside `artifacts.atomic_target(final)`, passes the temp `dest`, fails the step if `dest` is missing, then saves title and duration. Do not change.
- `app/worker.py` -- `build_adapters` only accepts `fake` today (and `verifier = "none"`); extend the downloader branch.
- `app/config.py` -- `ROLES`, validation; add the `[ytdlp]` table with `js_runtime`. `config.toml` has commented real names.
- `app/checks.py` -- already reports ffmpeg and the JS runtime at startup. Reuse, do not duplicate.
- `tests/test_pipeline.py`, `tests/test_startup.py` use `GOOD` (`config.toml`) for `main.run()` and adapter building; they will need fake-provider configs once the default changes.
- `pyproject.toml` -- add the pinned dependency and the `network` pytest marker.
- New: `app/adapters/ytdlp.py`, `tests/conftest.py`, `tests/test_ytdlp.py`.

## Tasks & Acceptance

**Execution:**
- [ ] `pyproject.toml` -- add `yt-dlp[default]` pinned; register the `network` marker; refresh `uv.lock` -- dependency
- [ ] `app/adapters/ytdlp.py` -- `YtDlpDownloader(js_runtime, ydl_factory=None)` implementing the port with the rules above; the factory parameter lets tests inject a fake yt-dlp -- adapter
- [ ] `app/config.py`, `config.toml` -- `[ytdlp] js_runtime`, validated; default `downloader = "yt-dlp"` -- config
- [ ] `app/core/youtube.py`, `tests/test_submit.py` -- accept scheme-less links; tests for the rows above -- Story 1.2 fix
- [ ] `app/worker.py` -- build the adapter for `yt-dlp`, keep `fake`, reject unknown -- wiring
- [ ] `tests/conftest.py` -- autouse network block unless marked `network` -- offline guarantee
- [ ] `tests/test_ytdlp.py` -- every matrix row using the injected fake yt-dlp; one `network`-marked smoke test on a short public video -- verification
- [ ] Existing tests -- switch those that run the worker or `main.run()` to fake-provider configs -- stay offline

**Acceptance Criteria:**
- Given a valid video, when the step runs, then one m4a is at the pipeline's path, the title and duration are saved on the Episode, and no scratch files remain.
- Given a private, removed or live video, when the step runs, then the Job fails with a readable reason and `retryable` false.
- Given a bot check, network error or missing ffmpeg, when the step runs, then the Job fails with a readable reason and `retryable` true.
- Given any failure, when the episode folder is listed, then it holds no partial audio and no scratch folder.
- Given the full offline suite, when it runs with networking blocked, then it passes.
- Given `RUN_NETWORK_TESTS=1`, when the smoke test runs, then a real short video downloads as a playable m4a.

## Implementation Notes

- Built as specified: `yt-dlp[default]==2026.8.19` pinned, `app/adapters/ytdlp.py` (`YtDlpDownloader`, `classify_error`), `[ytdlp] js_runtime` in config, `downloader = "yt-dlp"` default, adapter wiring in `build_adapters`, `tests/conftest.py` network block, `tests/test_ytdlp.py`, `tests/fake_config.toml` for offline `main.run()` tests, scheme-less URL parsing.
- yt-dlp's JS runtime option is `js_runtimes={"node": {}}` (checked in yt-dlp source). Live and upcoming streams are skipped by a `match_filter` and mapped to a non-retryable error.
- Verified live with the app on the supplied link: real title, 3929 s, AAC m4a, no scratch folder. The opt-in smoke test (`RUN_NETWORK_TESTS=1 uv run pytest -m network`) passes.
- Review patches: classification now checks bot and try-again-later messages before the permanent list, with anchored phrases ("has been removed", not "removed"); any `YoutubeDLError` maps to a `StepError`; disk-full and permission errors get their own message instead of the network hint; an empty m4a or a non-finite duration fails before anything is published; scheme detection only looks at the start of the text (a raw `https://` inside the query no longer breaks a scheme-less link); the network block also stops DNS lookups; tests pin exact IDs, classification order, and that `fake_config.toml` matches `config.toml`.

## Spec Change Log

## Review Triage Log

| Finding | Verdict | Route | Evidence |
|---|---|---|---|
| Loose substrings classify transient errors as permanent ("removed", "is unavailable", "try again") | medium | patch | Order and phrases changed; ordering test added |
| Other `YoutubeDLError` subclasses escape unmapped | medium | patch | Now caught as `YoutubeDLError` |
| Disk-full or permission OSError reported as network problem | medium | patch | Own message and test |
| Empty m4a published as done | medium | patch | Size check before `os.replace` |
| NaN or inf duration raises after the file is published | low | patch | Validated and rounded before publishing |
| Scheme-less link with raw `https://` in its query rejected | medium | patch | Scheme test now anchored to the start; test added |
| Schemeless test accepted either ID | medium | patch | Parametrized with exact expected IDs |
| Network block misses DNS | low | patch | `getaddrinfo` blocked too |
| `fake_config.toml` may drift from `config.toml` | low | patch | Comparison test added |
| Real yt-dlp contract (options, postprocessor, filter signature) only checked by opt-in network test | medium | defer | Recorded in deferred-work; needs a local-source test |
| Fixed shared scratch folder could collide | low | rejected | One worker at a time (AD-6) |
| `[ytdlp]` table required for existing configs; no README; pin has no update doc | low | rejected | Single user; README already deferred |
| No duration or size cap, no overall timeout | low | rejected | Spec sets no bound; 6 h episodes expected |
| Live-stream handling has three paths; `str(exc)` drops type; skipped-for-other-reason retryable | low | rejected | No named harm |
| Port in scheme-less link, `//youtube.com`, import order, `test_layering` scope | low | rejected | Unlikely or style |

## Verification

**Commands:**
- `uv run pytest -q` -- expected: all tests pass offline
- `RUN_NETWORK_TESTS=1 uv run pytest -q -m network` -- expected: the real download test passes
- `uv run podcast-synthesis`, submit a short public video -- expected: the Episode shows the real title, `download: done`, and `data/episodes/<id>/audio.m4a` plays
