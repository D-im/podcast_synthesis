
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
