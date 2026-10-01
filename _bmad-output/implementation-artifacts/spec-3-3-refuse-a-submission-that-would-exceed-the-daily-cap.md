---
title: 'Story 3.3: Refuse a submission that would exceed the Daily Cap'
type: 'feature'
created: '2026-10-01'
status: 'done'
baseline_commit: 'cc6c1a5'
route: 'dispatch'
review_loop_iteration: 0
---

## Intent

A new submission first gets a free metadata lookup (title, duration), then the whole Job (transcribe, summarize, verify) is estimated from that duration and the prices in `config.toml`. If today's actual spend plus the estimate is within the Daily Cap the Episode and Job are created with the estimate stored; otherwise it is refused with the cap, today's spend, the estimate and the reason, and nothing is created. Regeneration (Story 2.4) is refused the same way. The estimate and the actual cost of the latest run are shown on the Episode.

## Decisions (built without a separate approval round, at the user's request to keep building)

- `app/core/estimate.py`: `estimate_job(duration_seconds, prices)` extends the 2.4 estimator. Transcript size is assumed as 18 characters per second of audio (the 65-minute real run measured about 15), transcription cost is duration times `[pricing] transcription_usd_per_hour`, and a fake transcriber costs $0. `Estimate.transcribe_micro` and `Prices.transcription_usd_per_hour` / `transcriber_provider` are new defaulted fields, so the 2.4 numbers are unchanged.
- `app/core/budget.py`: `check_budget(today, estimate, cap)`, pure, allowed only while today is below the cap and today plus the estimate fits. Spend at or above the cap refuses anything. Story 3.4 reuses it before each paid step.
- `app/core/admission.py`: `admit()` does the lookup, estimate and check without writing anything. A failed lookup or unreadable config refuses the submission (the cost cannot be estimated) and creates nothing. An Episode that already exists skips all of this.
- `YtDlpDownloader.lookup` (`skip_download`, rejects live streams and unknown durations) and `FakeDownloader.lookup`. `main.py` passes the downloader's `lookup` to `create_app`; without one (tests), submissions are unchanged.
- Migration 10: `jobs.estimate_micro`. Stored at submission and at regeneration. The Episode page shows "This run: estimate $x, actual so far $y", the actual being ledger spend since the Job was created.
- The cap is read from `config.toml` on every submission and regeneration, so a change applies to the next one (default $5). The header still shows the cap read at start-up.
- Refusals return HTTP 409 with the message; the regeneration page text changed from "not enforced yet" to "Confirming will be refused".

## Verification

- `uv run pytest -q`: 725 passed, 7 skipped, offline. Not done: no real yt-dlp lookup against the network, no code-review pass (story is in `review`).
