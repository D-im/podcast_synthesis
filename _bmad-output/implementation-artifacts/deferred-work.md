
- source_spec: `_bmad-output/implementation-artifacts/spec-1-1-start-the-app-with-one-command.md`
  summary: Add a README and `.env.example` documenting the three API key names, host tools (ffmpeg, Node 22+ or Deno 2.3+) and the start command.
  evidence: Review found no setup documentation; a fresh checkout has no record of required keys or tools outside the planning docs.
- source_spec: `_bmad-output/implementation-artifacts/spec-1-1-start-the-app-with-one-command.md`
  summary: Decide SQLite `foreign_keys=ON` and `busy_timeout` when tables and the worker thread arrive (unverified, medium if real).
  evidence: Connections set only WAL now. Once the worker writes while web reads (Stories 1.2 and 1.3), missing busy timeout could surface as 'database is locked'.

- source_spec: `_bmad-output/implementation-artifacts/spec-1-6-download-audio.md`
  summary: Add a default-run test that exercises the real `yt_dlp.YoutubeDL` option set (outtmpl, m4a postprocessor, match_filter signature) against a local source instead of only the opt-in network test.
  evidence: Every default test replaces yt-dlp with a fake that writes the expected file; a yt-dlp upgrade or option typo could break real downloads with a green offline suite.

- source_spec: `_bmad-output/implementation-artifacts/spec-1-8-generate-the-one-pager.md`
  summary: Decide how to handle Episodes that have a legacy `one_pager.json` but no `one_pager_versions` row (adopt as v1, or re-run summarize).
  evidence: `finished("summarize")` and the page look only at versioned files and rows; re-queuing such an Episode re-runs summarize and spends money, and the page shows a notice instead of the One-Pager. Affects only data created before Story 1.8.

- source_spec: `_bmad-output/implementation-artifacts/spec-1-11-first-supervised-real-run.md`
  summary: Find the root cause of the app's 59 MB AssemblyAI submit failing after about 16 minutes (opaque error at the time), and consider uploading from a faster path.
  evidence: Compressed audio uploads fine (16.8 MB in about 220 s) and the failure message now includes the vendor reason, so a recurrence will be diagnosable; upload speed to the vendor was about 75 KB/s against a 15.8 Mbps link.
- source_spec: `_bmad-output/implementation-artifacts/spec-1-11-first-supervised-real-run.md`
  summary: Consider a prompt change so long episodes get a per-section length allowance and fewer dropped sub-topics (the first real One-Pager omitted Argentina, the deficit argument and the youth-vote figures).
  evidence: 28-claim check in `real-run-1-11.md` found no invented claims but several missed topics; the Epic 2 coverage check will measure this.

- source_spec: `_bmad-output/implementation-artifacts/spec-2-2-check-coverage-and-flag-low-scores.md`
  summary: Calibrate coverage: decide how strict "partial" should be, whether the ideas pass should list more than 15 ideas, and what threshold makes a flag useful.
  evidence: First real check gave coverage 0.60 (3 covered, 12 partial, 0 missing) for a faithful one-page summary of a 65 minute interview, so the 0.75 default flags nearly everything; Argentina and the youth-vote figures were never listed as ideas. Revisit once Verdicts (Story 2.5) show what the scores mean in practice.

## Deferred from: code review of story-3.1 (2026-10-01)

- Two app instances at once: the second start-up `recover()` resets a Job the first instance is running, and its worker can start it before the port bind fails (possible duplicate paid calls). Needs a single-instance lock. Unverified in practice; would be medium.
