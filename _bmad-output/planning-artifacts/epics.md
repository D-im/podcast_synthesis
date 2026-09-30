---
stepsCompleted: ['step-01-validate-prerequisites', 'step-02-design-epics', 'step-03-epic-1-stories', 'step-03-epic-2-stories', 'step-03-epic-3-stories', 'step-04-final-validation']
inputDocuments:
  - _bmad-output/planning-artifacts/prds/prd-podcast_synthesis-2026-09-30/prd.md
  - _bmad-output/planning-artifacts/prds/prd-podcast_synthesis-2026-09-30/addendum.md
  - _bmad-output/planning-artifacts/architecture/architecture-podcast_synthesis-2026-09-30/ARCHITECTURE-SPINE.md
  - _bmad-output/planning-artifacts/architecture/architecture-podcast_synthesis-2026-09-30/OVERVIEW.md
---

# podcast_synthesis - Epic Breakdown

## Overview

This document provides the complete epic and story breakdown for podcast_synthesis, decomposing the requirements from the PRD and Architecture requirements into implementable stories. No UX design document exists. The UI is a simple local web page.

## Requirements Inventory

### Functional Requirements

FR-1: Submit an Episode. Diimrem can submit a YouTube URL and start a Job. Invalid or non-YouTube URLs are rejected before any spend. A URL already submitted shows the existing Episode.
FR-2: Download Audio. The system downloads and stores the Episode's Audio (1 to 3 hours typical, up to 6). A download failure puts the Job in a failed state with the reason shown.
FR-3: Transcribe. The system produces and stores a Transcript from the Audio, up to 6 hours. The Transcript is viewable.
FR-4: Generate the One-Pager (and regenerate it on request, manually, keeping every version). The system generates a One-Pager from the Transcript using editable prompt files (the section list is declared in the summarize prompt), and stores it. Each version records which prompts produced it. Long Transcripts still produce a single One-Pager.
FR-5: Show Job status and recover from failure. The page shows each Job's step (queued, downloading, transcribing, summarizing, verifying, done, failed). A retry resumes at the failed step without repeating completed steps or their spend.
FR-6: Score One-Pager fidelity. A different LLM checks the One-Pager against the Transcript and stores a Fidelity Score (accuracy and coverage separately, with listed issues). Low scores are flagged by a configurable threshold. It has its own spend, status and retry. It is advisory only.
FR-7: Browse Episodes. Diimrem can list Episodes with title, date, status and Verdict, and open one to see the One-Pager and Fidelity Score with links to Transcript and Audio. Text search across titles and One-Pagers.
FR-8: Record a Verdict. Diimrem can record a worth-it rating with a free-text reason. An Episode can carry several Verdicts, and the latest is shown. Verdicts are stored for later analysis.
FR-9: Track Spend. The system records the spend of each step of each Job. Each Episode shows total and per-step spend, and a page shows today's spend against the Daily Cap.
FR-10: Enforce the Daily Cap. The system refuses to start a new Job when today's spend has reached the cap (configurable, default $5). It estimates a Job's cost, including the fidelity check, from the Episode's duration and refuses if it would exceed the cap. A refused submission explains why and leaves no partial Job.
FR-11: Isolate providers. The pipeline reaches transcription and summarization only through configurable provider interfaces. Changing provider requires no change to Library, Verdict or Spend features.

### NonFunctional Requirements

Derived from the PRD and spine, since the PRD has no separate NFR section.

NFR1: A Job interrupted by sleep or restart resumes without repeating completed steps or paying twice for the same transcription.
NFR2: The app runs on a single machine (laptop or Mac mini) with one command, and handles 6-hour episodes.
NFR3: The server binds to localhost only, with no authentication.
NFR4: Secrets are read from the environment or a git-ignored `.env` file. Logs never contain API keys or transcript text.
NFR5: All data lives under one `data/` folder, which is the complete backup.
NFR6: Money is stored as integer micro-dollars. Timestamps are UTC in storage, and the cap day is the local calendar day.

### Additional Requirements

- No starter template. Greenfield Python project managed with uv.
- Structure: `app/web`, `app/core`, `app/ports`, `app/adapters`, `app/store`, `app/worker.py`, `config.toml`, `prompts/`, `data/`.
- AD-1: Stored step state is the source of truth. Finished steps are skipped on re-run.
- AD-2: Core imports only ports. Providers named in config.
- AD-3: Only the worker thread mutates step state. Web enqueues, reads and writes Verdicts.
- AD-4: Write artifacts to a temp name, rename, then mark the step done.
- AD-5: On startup, running Jobs return to queued. A vendor job ID is saved before waiting on a long operation, and resumed on re-run.
- AD-6: One Job at a time, in submission order.
- AD-7: Every paid call goes through the `Meter`, which appends to an append-only spend ledger.
- AD-8: A single `check_budget` runs before each paid step. Over the cap, the Job moves to `paused` and keeps artifacts.
- AD-9: Episode identity is the YouTube video ID. Artifacts live in `data/episodes/<video_id>/`.
- AD-10: SQLite (WAL) for state, files for artifacts.
- AD-11: The Transcriber returns one normalized timestamped Transcript. A YouTube captions fallback is deferred.
- AD-12: Summarize the whole Transcript in one call, with map-reduce above a configured token limit. No embeddings.
- AD-13: The Verifier uses a different vendor from the Summarizer, enforced by config validation. Full Transcript plus One-Pager in, claim check and main-idea comparison out.
- AD-14: One-Pagers and Fidelity Scores are versioned and all versions are kept. A Verdict stores the version it rated. Each version records the hash of every prompt used, and the models.
- AD-15: Bind to 127.0.0.1, no auth.
- AD-16: Prompts are plain-text files in `prompts/` (`summarize`, `summarize_map`, `summarize_reduce`, `verify_claims`, `verify_coverage`). The section list is declared in `summarize`, and the model returns structured output rendered by section. Files are read at job time. The Verifier's prompts are separate from the Summarizer's. Content hashes are recorded per version.
- Stack: Python 3.12+, FastAPI, Uvicorn, Jinja2, htmx 2.x, stdlib sqlite3 (no ORM), yt-dlp with `yt-dlp[default]`, ffmpeg and Node 22+ or Deno 2.3+ on the host.
- Providers: AssemblyAI (transcription), Claude Sonnet 5.5 (One-Pager), OpenAI gpt-6.1-sol (checker). ElevenLabs Scribe v2 is the fallback transcription adapter.
- Deferred: auth, audio pruning, semantic search, local model adapters, packaging and auto-start.

### UX Design Requirements

None. No UX design document exists.

### FR Coverage Map

### FR Coverage Map

FR-1: Epic 1 - Submit a YouTube URL, reject invalid, dedupe
FR-2: Epic 1 - Download and store Audio
FR-3: Epic 1 - Transcribe and store Transcript
FR-4: Epic 1 - Generate One-Pager from editable prompts
FR-5: Epic 1 (status display) and Epic 3 (retry and resume)
FR-6: Epic 2 - Independent fidelity check
FR-7: Epic 1 - Library, search, Episode view
FR-8: Epic 2 - Record Verdicts
FR-9: Epic 1 - Spend ledger and display
FR-10: Epic 3 - Daily Cap and pre-job estimate
FR-11: Epic 1 - Provider ports and adapters

## Epic List

### Epic 1: From a YouTube link to a readable One-Pager
Paste a link, watch it progress, and read the One-Pager in a library of past Episodes, end to end through the real vendors, with every dollar recorded.
**FRs covered:** FR-1, FR-2, FR-3, FR-4, FR-7, FR-9, FR-11, FR-5 (status display)

### Epic 2: Know whether to trust it, and record what was worth it
An independent model checks each One-Pager against its Transcript, and Diimrem records Verdicts with reasons.
**FRs covered:** FR-6, FR-8

### Epic 3: Safe to leave running
Interrupted and failed Jobs resume without repeating or repaying for finished work, and the Daily Cap stops spend before it runs away.
**FRs covered:** FR-5 (retry and resume), FR-10

## Epic 1: From a YouTube link to a readable One-Pager

Paste a link, watch it progress, and read the One-Pager in a library of past Episodes, end to end through the real vendors, with every dollar recorded. Covers FR-1, FR-2, FR-3, FR-4, FR-7, FR-9, FR-11 and the status display of FR-5.

### Story 1.1: Start the app with one command

As Diimrem,
I want to start the whole app with one command on my machine,
So that I have a running local page to build everything else on.

**Acceptance Criteria:**

**Given** a fresh checkout with Python 3.12+, uv, ffmpeg and Node 22+ or Deno 2.3+ installed
**When** I run the documented start command
**Then** the web server starts and serves a home page at `http://127.0.0.1:<port>`
**And** the server refuses connections from other machines because it is bound to `127.0.0.1` only (NFR3, AD-15)

**Given** no `data/` folder exists
**When** the app starts
**Then** `data/` and the SQLite database are created, with WAL mode on (AD-10, NFR5)
**And** a second start reuses the existing data without losing it

**Given** API keys are set in the environment or a git-ignored `.env`
**When** the app starts
**Then** it reads them without printing them
**And** if a required key is missing, it starts anyway and names the missing key in a message, because not every story needs every key (NFR4)

**Given** `config.toml` and `prompts/` exist
**When** the app starts
**Then** it loads the Daily Cap, provider and model names, token limit and fidelity threshold from `config.toml`
**And** `.env` and `data/` are listed in `.gitignore`

**Given** ffmpeg or the JavaScript runtime is missing
**When** the app starts
**Then** it reports which tool is missing and where it checked, without crashing

### Story 1.2: Submit a YouTube link

As Diimrem,
I want to paste a YouTube link and start processing,
So that I can get a one-pager without any manual steps.

**Acceptance Criteria:**

**Given** the home page
**When** I paste a valid YouTube URL (watch, short and `youtu.be` forms) and submit
**Then** an Episode is created with the video ID as its identity (AD-9)
**And** a Job is created with its four steps (`download`, `transcribe`, `summarize`, `verify`) each persisted as `pending` (AD-1)
**And** the Job is queued and I am shown the Episode (FR-1)

**Given** a string that is not a YouTube URL
**When** I submit it
**Then** it is rejected with a clear message
**And** no Episode, Job or spend is created

**Given** a URL for a video that already has an Episode, in any URL form
**When** I submit it
**Then** I am taken to the existing Episode
**And** no duplicate Episode or Job is created

**Given** the tables this story needs
**When** the story is complete
**Then** only `episodes`, `jobs` and `steps` exist, created by this story and not before

### Story 1.3: Run a Job end to end on fake providers

As Diimrem,
I want a queued Job to run through all four steps using fake providers,
So that the pipeline, state handling and provider boundaries are proven before any real API call or spend.

**Acceptance Criteria:**

**Given** the ports `Downloader`, `Transcriber`, `Summarizer` and `Verifier` in `app/ports/`
**When** core code runs a step
**Then** it imports only the ports and never a vendor (AD-2, FR-11)
**And** provider and model names come from `config.toml`

**Given** fake adapters for each port that return fixed, deterministic results at no cost
**When** a Job is queued and the worker runs
**Then** the four steps run in order and each step's state moves `pending`, `running`, `done` in the database (AD-1)
**And** the Job ends as `done`

**Given** the worker thread
**When** two Jobs are queued
**Then** the second does not start until the first finishes, in submission order (AD-6)
**And** only the worker changes step state, while the web layer only reads and enqueues (AD-3)

**Given** a step produces an artifact
**When** it saves the file
**Then** it writes a temp file in the same folder, renames it, and only then marks the step `done` (AD-4)
**And** artifacts live in `data/episodes/<video_id>/`

**Given** a step whose artifact already exists and is marked `done`
**When** the Job is run again
**Then** that step is skipped (AD-1)

**Given** a fake adapter configured to fail
**When** its step runs
**Then** the step is `failed`, the error message and a `retryable` flag are stored, and later steps do not run
**And** the error text contains no secrets or transcript text

**Given** the fakes exist
**When** the test suite runs
**Then** every later story can run its acceptance checks against the fakes with no network and no spend

### Story 1.4: Watch a Job's progress

As Diimrem,
I want to see what step a Job is on and whether it failed,
So that I know if it's working without checking logs.

**Acceptance Criteria:**

**Given** a Job is running
**When** I open its Episode page
**Then** I see its current status: queued, downloading, transcribing, summarizing, verifying, done or failed (FR-5)
**And** the status updates on its own every few seconds through htmx polling, with no full page reload

**Given** a Job has failed
**When** I view it
**Then** I see which step failed and the stored error message

**Given** a Job is done
**When** I view it
**Then** polling stops
**And** the status reads `done`

### Story 1.5: See what every run costs

As Diimrem,
I want every paid call recorded with its cost,
So that I always know what I've spent and can trust the cap later.

**Acceptance Criteria:**

**Given** the `Meter` wrapper
**When** any adapter makes a paid call
**Then** it goes through the `Meter`, which appends a ledger row with episode, step, provider, amount and timestamp (AD-7)
**And** the ledger is append-only, with no update or delete path in the code

**Given** amounts
**When** they are stored
**Then** they are integer micro-dollars (NFR6)
**And** timestamps are UTC

**Given** an Episode with recorded spend
**When** I open it
**Then** I see its total spend and a per-step breakdown (FR-9)

**Given** any page
**When** I look at the header or a spend page
**Then** I see today's spend, using the local calendar day, against the Daily Cap from config

**Given** the fake adapters
**When** they are configured with a fake cost
**Then** those costs appear in the ledger, so cost reporting can be tested without spending

### Story 1.6: Download audio

As Diimrem,
I want the audio of a video downloaded and stored,
So that it can be transcribed and kept.

**Acceptance Criteria:**

**Given** a queued Job for a valid video
**When** the `download` step runs with the yt-dlp adapter
**Then** the audio is stored in the Episode's folder using a temp file then rename (AD-4)
**And** the Episode's title and duration are saved, because later cost estimates need the duration (FR-2)

**Given** a video of up to 6 hours
**When** it is downloaded
**Then** it completes without loading the whole file into memory

**Given** yt-dlp fails (video unavailable, private, or YouTube blocks the download)
**When** the step fails
**Then** the Job is `failed` with a readable reason and a retryable flag
**And** no partial audio file is left marked as done

**Given** yt-dlp is pinned to a version in the project
**When** the step runs
**Then** it uses the pinned version with `yt-dlp[default]` and the configured JavaScript runtime

### Story 1.7: Transcribe

As Diimrem,
I want the audio transcribed,
So that a one-pager can be written from the full text.

**Acceptance Criteria:**

**Given** a Job with `download` done
**When** the `transcribe` step runs with the AssemblyAI adapter
**Then** it submits the audio, saves the vendor job ID to the database before waiting (AD-5), and polls until finished
**And** the result is stored as one normalized, timestamped Transcript in the app's own format, with no vendor-specific shape leaking out (AD-11)

**Given** the transcription call
**When** it completes
**Then** its cost is recorded through the `Meter` (AD-7)

**Given** a finished Transcript
**When** I open the Episode
**Then** I can view the Transcript (FR-3)

**Given** a 6-hour audio file
**When** it is transcribed
**Then** it is accepted by the vendor without splitting, and the normalized Transcript covers the whole duration

**Given** the vendor returns an error
**When** the step fails
**Then** the Job is `failed` with the vendor's message (without secrets) and a retryable flag

### Story 1.8: Generate the One-Pager

As Diimrem,
I want a one-page synthesis written from the transcript,
So that I can read the big ideas and action items instead of listening.

**Acceptance Criteria:**

**Given** a Job with `transcribe` done
**When** the `summarize` step runs with the Anthropic adapter
**Then** the whole Transcript is sent in a single call using the `summarize` prompt file (AD-12, AD-16)
**And** the prompt file is read from disk at job time, so an edit applies to the next run without a restart

**Given** the `summarize` prompt declares a section list (for example Big Ideas and Actionable Items)
**When** the model responds
**Then** the response is structured output with those named sections
**And** the web page renders by section and never parses free text

**Given** a generated One-Pager
**When** it is saved
**Then** it is stored as version 1 for the Episode (AD-14)
**And** the version records the content hash of each prompt file used and the model name
**And** the cost is recorded through the `Meter`

**Given** the One-Pager is stored
**When** I open the Episode
**Then** I can read it (FR-4)

**Given** the model's output does not match the declared sections
**When** the step processes it
**Then** the step fails with a retryable error rather than saving a malformed One-Pager

### Story 1.9: Handle very long transcripts

As Diimrem,
I want very long episodes to still produce one One-Pager,
So that a 5 hour episode doesn't fail or get cut off.

**Acceptance Criteria:**

**Given** a Transcript at or below the configured token limit
**When** `summarize` runs
**Then** the single-call path from Story 1.8 is used

**Given** a Transcript above the configured token limit
**When** `summarize` runs
**Then** it summarizes sections with the `summarize_map` prompt and then synthesizes them with the `summarize_reduce` prompt
**And** the result is one One-Pager with the same sections and metadata as the single-call path (AD-12)

**Given** the token limit
**When** it is changed in `config.toml`
**Then** the next run uses the new limit

**Given** the map-reduce path
**When** it completes
**Then** every model call in it is recorded in the ledger
**And** the version records the hash of all three prompt files used

**Given** the fake `Summarizer`
**When** the tests run
**Then** both paths are tested without spend

### Story 1.10: Browse the library and read an episode

As Diimrem,
I want a list of past episodes I can search and open,
So that every one-pager is a reference I can find later.

**Acceptance Criteria:**

**Given** processed Episodes
**When** I open the library
**Then** I see each Episode's title, date, status and latest Verdict, if any (FR-7)

**Given** an Episode
**When** I open it
**Then** I see the latest One-Pager, its status, its spend, and links to the Transcript and Audio
**And** if a Fidelity Score or Verdict exists, it is shown too

**Given** text typed in the search box
**When** I search
**Then** matching Episodes are found by title and One-Pager text, using plain text search with no embeddings

**Given** an Episode is still processing or failed
**When** I view the library
**Then** it appears with its status rather than being hidden

### Story 1.11: First supervised real run

As Diimrem,
I want to run one real one-hour episode through every real vendor and record what it cost,
So that the pipeline is proven for real and I can set the Daily Cap with data.

**Acceptance Criteria:**

**Given** real API keys and a one-hour episode I choose
**When** I submit it with the real adapters configured
**Then** the Job reaches `done` with a real Transcript and One-Pager

**Given** the run completes
**When** I open the spend page
**Then** I see the actual cost per step and in total
**And** the actual cost is written down as the basis for deciding the Daily Cap (PRD open question 2)

**Given** no real `Verifier` is configured yet (it arrives in Epic 2)
**When** the Job reaches the `verify` step
**Then** the step is marked `skipped` with the reason "no verifier configured", and the Job still ends `done`

**Given** the run
**When** I read the One-Pager against what I know of the episode
**Then** I note whether it is faithful, as a first informal check of the core assumption

**Given** the real run
**When** it is finished
**Then** no API key or transcript text appears in the logs (NFR4)


## Epic 2: Know whether to trust it, and record what was worth it

An independent model checks each One-Pager against its Transcript, and Diimrem records Verdicts with reasons. Covers FR-6 and FR-8, plus manual regeneration of a One-Pager.

### Story 2.1: Check claims against the transcript

As Diimrem,
I want a second, independent model to check each claim in the One-Pager against the transcript,
So that I can tell whether the one-pager states anything the episode didn't say.

**Acceptance Criteria:**

**Given** the `Verifier` is configured with the OpenAI adapter
**When** config is loaded
**Then** startup validation rejects a configuration where the Verifier's vendor is the same as the Summarizer's, with a clear message (AD-13)

**Given** a Job whose `summarize` step is done
**When** the `verify` step runs
**Then** it sends the full Transcript and the One-Pager version to the Verifier using the `verify_claims` prompt file (AD-16)
**And** the `verify_claims` prompt is a separate file from the `summarize` prompt

**Given** the Verifier's response
**When** it is stored
**Then** each claim from the One-Pager has a verdict of supported or unsupported, with quoted transcript evidence for supported claims
**And** an accuracy score is stored, with the list of unsupported claims
**And** it is attached to the One-Pager version it checked (AD-14), recording the prompt hash and model name
**And** the cost is recorded through the `Meter`

**Given** a stored Fidelity Score
**When** I open the Episode
**Then** I see the accuracy score and the unsupported claims beside the One-Pager (FR-6)

**Given** the Transcript is longer than the checker model's input limit
**When** the `verify` step starts
**Then** the step ends `skipped` with the reason "transcript too long for the checker", and the Job still ends `done`
**And** nothing is sent to the vendor

**Given** the checker fails
**When** the step errors
**Then** the One-Pager is unaffected and still readable, because the check is advisory (AD-13)
**And** the step records a retryable error

### Story 2.2: Check coverage of the main ideas and flag low scores

As Diimrem,
I want the checker to also tell me whether the one-pager missed major ideas,
So that I can spot a one-pager that is accurate but incomplete.

**Acceptance Criteria:**

**Given** a Job in the `verify` step
**When** the checker runs its coverage pass with the `verify_coverage` prompt file
**Then** it reads the Transcript and independently lists the main ideas before seeing the One-Pager's content
**And** then compares that list to the One-Pager

**Given** the comparison
**When** it is stored
**Then** a coverage score is stored separately from the accuracy score
**And** the main ideas missing from the One-Pager are listed

**Given** accuracy and coverage thresholds in `config.toml`
**When** either score is below its threshold
**Then** the Episode is visibly flagged in the library and on the Episode page

**Given** a flagged Episode
**When** I open it
**Then** the One-Pager is still shown normally, and the flag never blocks, hides or rewrites it (AD-13)

**Given** a threshold changed in config
**When** the library is viewed
**Then** flags use the new threshold without re-running any check

**Given** both passes
**When** they run
**Then** both costs are recorded separately through the `Meter`

### Story 2.3: Prove the checker catches planted errors

As Diimrem,
I want evidence that the checker catches known errors,
So that I can trust its scores.

**Acceptance Criteria:**

**Given** a short fixture Transcript committed to the repo with a known set of main ideas
**When** a fixture One-Pager is built from it with N deliberately false claims and one major idea left out
**Then** both are stored as test fixtures with the planted errors documented

**Given** the plumbing tests using the fake `Verifier`
**When** the test suite runs
**Then** storing, display and flagging are tested with no network and no spend

**Given** a manual, clearly labelled paid test that runs the fixtures through the real checker
**When** I run it
**Then** it reports which planted false claims were flagged as unsupported, whether the omitted idea was listed as missed, and how many true claims were wrongly flagged
**And** it prints its own cost, so I can see what a check costs

**Given** the results
**When** I review them
**Then** I write down what the checker caught and missed, as the basis for how far to trust the Fidelity Score (PRD open question 1)

### Story 2.4: Regenerate a One-Pager on request

As Diimrem,
I want to regenerate a One-Pager for an episode only when I choose to,
So that I can test a prompt change without anything rerunning by itself or spending money unexpectedly.

**Acceptance Criteria:**

**Given** an Episode with a stored Transcript
**When** I edit a prompt file
**Then** nothing is regenerated and no spend occurs

**Given** an Episode page
**When** I click Regenerate
**Then** I see an estimated cost for `summarize` and `verify`, calculated from the stored Transcript's token count and the model prices in `config.toml`, before anything starts
**And** nothing starts until I confirm
**And** the estimate is produced by a cost estimator that this story creates, which Story 3.3 later extends

**Given** I confirm
**When** the Job runs
**Then** it re-runs only `summarize` and `verify` on the existing Transcript, without downloading or transcribing again
**And** the result is stored as the next version of the One-Pager and its Fidelity Score (AD-14)

**Given** several versions of an Episode's One-Pager
**When** I open the Episode
**Then** the latest is shown, and I can open every earlier version
**And** each version shows the date, models and which prompt files changed compared with the previous version (from the stored hashes) (AD-16)

**Given** a regeneration fails
**When** the step errors
**Then** the previous versions are untouched and still shown

**Given** a regeneration is queued
**When** another Job is running
**Then** it waits its turn like any other Job (AD-6)

### Story 2.5: Record a Verdict

As Diimrem,
I want to record whether an episode was worth it and why,
So that I build a record of what earns my time.

**Acceptance Criteria:**

**Given** an Episode with a One-Pager
**When** I open it
**Then** I can record a Verdict with a worth-it or not-worth-it rating and a free-text reason (FR-8)

**Given** I submit a Verdict
**When** it is saved
**Then** it stores the One-Pager version that was being shown (AD-14), the rating, the reason and the time
**And** a Verdict without a reason is rejected with a message

**Given** an Episode with a Verdict
**When** I record another
**Then** both are kept, and the latest is shown as the current Verdict

**Given** the library
**When** I view it
**Then** each Episode shows its latest Verdict
**And** I can see the older Verdicts on the Episode page

**Given** an Episode with a Verdict on version 1 and a newer version 2
**When** I view it
**Then** the Verdict clearly shows which version it rated

**Given** Verdicts in the database
**When** the data is exported or queried later
**Then** each Verdict can be joined to its One-Pager version, Fidelity Score and prompt hashes


## Epic 3: Safe to leave running

Interrupted and failed Jobs resume without repeating or repaying for finished work, and the Daily Cap stops spend before it runs away. Covers the retry and resume half of FR-5, and FR-10.

### Story 3.1: Recover after sleep or restart

As Diimrem,
I want a Job interrupted by sleep, a crash or a restart to carry on by itself,
So that I can close my laptop and not lose work or pay twice.

**Acceptance Criteria:**

**Given** a Job left in `running` state because the app was stopped or crashed
**When** the app starts
**Then** every such Job is moved back to `queued` (AD-5)
**And** a step left in `running` is treated as not done, and any leftover temp files in its folder are removed (AD-4)

**Given** a Job in the `transcribe` step with a saved vendor job ID
**When** the Job is resumed
**Then** it resumes polling that same vendor job and does not submit the audio again
**And** the transcription cost is recorded once in the ledger, not twice (NFR1)

**Given** a Job interrupted during an LLM call (`summarize` or `verify`)
**When** the Job is resumed
**Then** that call is simply made again
**And** only completed calls appear in the ledger

**Given** the laptop sleeps while the worker is waiting on a vendor
**When** it wakes and the network is briefly unavailable
**Then** the worker retries with backoff instead of failing the Job, up to a configured limit

**Given** the fake adapters
**When** the tests simulate a kill mid-step and a restart
**Then** the Job completes, finished steps are not repeated, and the ledger has no duplicate entries for the same work

### Story 3.2: Retry a failed Job

As Diimrem,
I want to retry a failed Job and have it pick up where it failed,
So that one bad step doesn't cost me the steps that worked.

**Acceptance Criteria:**

**Given** a failed Job whose failed step is marked retryable
**When** I open it
**Then** I see the failed step, the error message and a Retry button (FR-5)

**Given** I click Retry
**When** the Job runs
**Then** steps already `done` are skipped, and the Job restarts at the failed step
**And** the ledger gains no new rows for skipped steps (FR-5)

**Given** a failure marked not retryable, such as a private video
**When** I open it
**Then** the error is shown without a Retry button, and the reason is explained

**Given** repeated failures
**When** I view the Job
**Then** I see how many attempts the failed step has had

**Given** a retry of a step that costs money
**When** the retry is queued
**Then** it is subject to the same budget check as any paid step (AD-8)

### Story 3.3: Refuse a submission that would exceed the Daily Cap

As Diimrem,
I want the app to refuse a new episode that would push me over my daily limit,
So that I'm never surprised by a bill.

**Acceptance Criteria:**

**Given** a submitted URL
**When** the app accepts it
**Then** it first does a metadata-only lookup of title and duration, which is free
**And** it estimates the cost of the whole Job, including the fidelity check, from that duration and the prices in `config.toml` (FR-10), using the estimator from Story 2.4 extended here

**Given** today's actual spend plus the estimate is within the Daily Cap
**When** I submit
**Then** the Episode and Job are created, and I see the estimate

**Given** today's actual spend plus the estimate would exceed the Daily Cap
**When** I submit
**Then** the submission is refused with a message showing the cap, today's spend, the estimate and the reason
**And** no Episode, Job or partial state is left behind (FR-10)

**Given** today's spend has already reached the Daily Cap
**When** I submit anything
**Then** it is refused for the same reason

**Given** the Daily Cap in `config.toml`
**When** I change it
**Then** the next submission uses the new value, and the default is $5

**Given** the cost estimator created in Story 2.4 for the summarize and verify steps
**When** a submission is estimated
**Then** this story extends it with the transcription cost, calculated from the video's duration and the transcription price in `config.toml`

**Given** the Regenerate feature from Story 2.4
**When** I confirm a regeneration that would exceed the cap
**Then** it is refused in the same way, using the same estimator

**Given** the estimator
**When** a real run finishes
**Then** the estimate and the actual cost are both visible on the Episode, so I can check the estimator and adjust the prices in config

### Story 3.4: Pause a Job that hits the cap mid-run, and resume it on request

As Diimrem,
I want a Job to stop cleanly if spend reaches the cap partway through,
So that I keep the work done so far and decide when to continue.

**Acceptance Criteria:**

**Given** a Job about to start a paid step
**When** the single `check_budget` function runs
**Then** it compares today's actual spend plus that step's estimate with the Daily Cap (AD-8)
**And** every paid step calls the same function, with no other place making this decision

**Given** the check fails
**When** the Job is at a step boundary
**Then** the Job moves to `paused` and keeps every artifact it has already produced
**And** the page shows it as paused because of the Daily Cap, with today's spend and the cap

**Given** a paused Job
**When** I raise the cap or the next local day starts
**Then** nothing resumes by itself
**And** I can click Resume, which is available once the budget allows the next step

**Given** I click Resume
**When** the Job runs
**Then** it continues from the paused step, skipping finished steps and never repeating paid work

**Given** a paused Job
**When** other Jobs are queued
**Then** the worker is not blocked by the paused Job
**And** a queued Job that starts and hits the cap at its first paid step also pauses

**Given** a step cost more than its estimate
**When** the next step's check runs
**Then** it uses actual spend, so the overrun is counted

**Given** the fake adapters with configured costs
**When** the tests run a Job that crosses the cap mid-run
**Then** it pauses at the right step, keeps its artifacts, and resumes correctly with no spend
