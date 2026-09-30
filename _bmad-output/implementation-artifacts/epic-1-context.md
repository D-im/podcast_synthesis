# Epic 1 Context: From a YouTube link to a readable One-Pager

<!-- Compiled from planning artifacts. Edit freely. Regenerate with compile-epic-context if planning docs change. -->

## Goal

Diimrem pastes a YouTube link, watches the Job progress, and reads the resulting One-Pager in a library of past Episodes. The flow runs end to end through the real vendors, and every dollar spent is recorded. This is the foundation for fidelity checking (Epic 2) and cap enforcement and recovery (Epic 3). Pipeline and boundaries are proven first on fake providers, then on one supervised real run.

## Stories

- Story 1.1: Start the app with one command
- Story 1.2: Submit a YouTube link
- Story 1.3: Run a Job end to end on fake providers
- Story 1.4: Watch a Job's progress
- Story 1.5: See what every run costs
- Story 1.6: Download audio
- Story 1.7: Transcribe
- Story 1.8: Generate the One-Pager
- Story 1.9: Handle very long transcripts
- Story 1.10: Browse the library and read an episode
- Story 1.11: First supervised real run

## Requirements & Constraints

- Only valid YouTube URLs (watch, short, youtu.be forms) are accepted. Invalid input is rejected with no Episode, Job or spend created. A URL for an existing video leads to the existing Episode.
- Audio for videos of 1 to 6 hours is downloaded and stored without loading the whole file in memory. Title and duration are saved, since cost estimates need duration.
- Transcription handles up to 6 hours without splitting on the app side. The Transcript is viewable.
- The One-Pager is generated from the Transcript using editable prompt files. Very long Transcripts still produce a single One-Pager. Regeneration is manual only (Epic 2).
- Job status is visible (queued, downloading, transcribing, summarizing, verifying, done, failed), updating without page reload. A failed Job shows the failed step and error.
- Library lists Episodes with title, date, status and latest Verdict. It has plain-text search over titles and One-Pager text. Processing and failed Episodes stay visible. The Episode view shows the One-Pager, spend, and links to Transcript and Audio.
- Spend is recorded per step per Episode. Totals and today's spend against the Daily Cap are shown.
- The app runs on one machine with one command. The server binds to localhost only, with no auth. Missing API keys or tools (ffmpeg, JS runtime) are reported by name and do not crash startup.
- Secrets come from the environment or a git-ignored `.env`. Logs never contain API keys or transcript text. Errors never contain secrets or transcript text.
- All data lives under `data/` (git-ignored), which is the complete backup.
- Every later story's acceptance checks must be runnable against fakes with no network and no spend.
- Story 1.11 has no real Verifier yet, so `verify` ends `skipped` ("no verifier configured") and the Job still ends `done`. Its actual cost data informs the Daily Cap choice.

## Technical Decisions

- Stack: Python 3.12+ with uv, FastAPI and Uvicorn, Jinja2, htmx 2.x (pinned, not 4), stdlib `sqlite3` in WAL mode with no ORM. yt-dlp is pinned, installed as `yt-dlp[default]`. ffmpeg and Node 22+ or Deno 2.3+ are host-installed. Greenfield project with no starter template.
- Layout: `app/web` (read, enqueue, record Verdicts), `app/core` (pipeline, step state machine, domain types), `app/ports`, `app/adapters` (one module per vendor), `app/store` (only layer touching SQLite or files), `app/worker.py` (composition root and worker thread), `config.toml`, `prompts/`, `data/`.
- Dependency rules: Core and Web never import Adapters. Core imports only ports. The worker wires adapters to ports. Provider and model names come from `config.toml`.
- Ports: `Downloader`, `Transcriber`, `Summarizer`, `Verifier`. Fake deterministic zero-cost adapters (with configurable fake cost and failure) are built in 1.3 and used by all tests.
- A Job has four ordered steps: `download`, `transcribe`, `summarize`, `verify`. Step states are `pending`, `running`, `done`, `failed`, `skipped`. Job states are `queued`, `running`, `paused`, `done`, `failed`. Persisted step state is the source of truth, and `done` steps are skipped on re-run.
- Only the single worker thread mutates step state, and Jobs run serially in submission order. Web only enqueues and reads, plus Verdict writes.
- Artifacts: write a temp file in the same folder, rename, then mark `done`. They live in `data/episodes/<video_id>/`.
- Episode identity is the YouTube video ID. Only the `episodes`, `jobs` and `steps` tables exist after 1.2, and later tables come with their stories.
- Failed steps store an error message and a `retryable` flag.
- Transcription: the vendor job ID is saved to the DB before polling, so a re-run resumes instead of resubmitting. The adapter returns one normalized timestamped Transcript in the app's own format, with no vendor shapes leaking.
- Summarize: send the whole Transcript in one call. Above the configured token limit, use map-reduce (`summarize_map` then `summarize_reduce`). There are no embeddings. The model returns structured output, with sections named in the `summarize` prompt file. The web renders by section and never parses free text. Malformed output fails the step as retryable.
- Prompts: plain-text files in `prompts/`, read from disk at job time so edits apply without a restart. Each One-Pager version stores the content hash of every prompt used and the model name. Versions are integer-numbered, and version 1 is created here.
- Spend: all paid calls go through the `Meter`, which appends to an append-only ledger with no update or delete path. Columns are episode, step, provider, amount and timestamp. Money is integer micro-dollars and timestamps are UTC. "Today" for the cap display uses the local calendar day. Every model call in map-reduce is ledgered.
- Providers: AssemblyAI (transcription), Claude Sonnet 5.5 (One-Pager). OpenAI is the Verifier, arriving in Epic 2.
- Config in `config.toml`: Daily Cap, provider and model names, token limit, fidelity threshold.

## UX & Interaction Patterns

- The UI is a simple local web page, server-rendered with Jinja2. Status polling uses htmx every few seconds and stops when the Job is `done`.
- The spend header or page shows today's spend against the Daily Cap on every page.

## Cross-Story Dependencies

- 1.2 creates the `episodes`, `jobs` and `steps` tables that 1.3 onward use. 1.1 supplies config, `.env` handling and the `data/` and DB bootstrap.
- 1.3 defines the ports, fakes and worker, and all later stories build on them. 1.5's `Meter` is used by the 1.6, 1.7, 1.8 and 1.9 adapters.
- 1.6 saves the duration that later cost estimates (Epic 3) need.
- 1.8's versioned One-Pager and prompt hashing are extended by 1.9, and by Epic 2 regeneration and Verdicts.
- 1.10 shows Fidelity Scores and Verdicts only if they exist, as those come from Epic 2.
- 1.11 depends on all earlier stories. Its cost findings feed the Daily Cap decision for Epic 3.
- Retry, resume, the Daily Cap and `paused` handling are Epic 3. Here, failures only store the error and `retryable` flag.
