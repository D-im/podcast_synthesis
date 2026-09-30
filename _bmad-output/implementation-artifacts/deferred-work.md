
- source_spec: `_bmad-output/implementation-artifacts/spec-1-1-start-the-app-with-one-command.md`
  summary: Add a README and `.env.example` documenting the three API key names, host tools (ffmpeg, Node 22+ or Deno 2.3+) and the start command.
  evidence: Review found no setup documentation; a fresh checkout has no record of required keys or tools outside the planning docs.
- source_spec: `_bmad-output/implementation-artifacts/spec-1-1-start-the-app-with-one-command.md`
  summary: Decide SQLite `foreign_keys=ON` and `busy_timeout` when tables and the worker thread arrive (unverified, medium if real).
  evidence: Connections set only WAL now. Once the worker writes while web reads (Stories 1.2 and 1.3), missing busy timeout could surface as 'database is locked'.
