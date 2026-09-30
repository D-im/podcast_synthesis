---
title: podcast_synthesis
status: final
created: 2026-09-30
updated: 2026-09-30
---

# PRD: podcast_synthesis
*Working title.*

## 0. Document Purpose
Hobby/solo PRD for a personal prototype, written for the builder and downstream planning (architecture, epics). Features are grouped with FRs nested and globally numbered. Inferred items are tagged `[ASSUMPTION]` and indexed in section 9. Technology choices are kept out of this document.

## 1. Vision
There is more worthwhile long-form content than there is time to consume. podcast_synthesis is a local web app for one person. Paste a YouTube link, and it downloads the audio, transcribes it, and produces a one-page **One-Pager** of the big ideas and actionable items. Every artifact is stored, so each item is a searchable reference.

The goal is optionality, not replacement. For each **Episode** you can listen, or you can trust the One-Pager. The One-Pager must be faithful enough that trusting it is a real option.

A lightweight **Verdict** (worth it, plus a reason) is captured after you've listened or read. It builds a record of which content earns your time, for a later scoring feature.

## 2. Target User
### 2.1 Jobs To Be Done
- Get the salient ideas of a 1 to 3 hour Episode (occasionally 4 to 6 hours) in minutes, without listening.
- Keep a durable, referable artifact for each Episode.
- Decide whether an Episode is worth listening to in full.
- Build a record of what was worth my time, and why.
- Keep spend predictable.

### 2.2 Non-Users (v1)
Anyone but the builder. There are no accounts, sharing or multi-user concerns.

### 2.3 Key User Journeys
- **UJ-1. Diimrem triages a new Episode.** Diimrem pastes a YouTube link on the local page. The Episode shows its progress (downloading, transcribing, summarizing) and lands on a finished One-Pager. Diimrem reads it, decides it covers what is needed, and moves on. The item stays in the library.
- **UJ-2. Diimrem gives a Verdict after listening.** Diimrem listens to an Episode the One-Pager flagged as interesting. Afterward Diimrem opens the item and records "worth it" with the reason: "the section on X changed how I think about Y". A later note on a missed point captures where the One-Pager fell short.
- **UJ-3. Diimrem hits the daily cap.** Diimrem pastes a sixth link after spending $5 today. The app declines to start the job and says why. Diimrem raises the cap or waits until tomorrow. **Edge case:** a job that fails midway retries without repaying for completed steps.

## 3. Glossary
- **Episode** — one piece of source content, identified by a YouTube URL. Has one Audio, one Transcript, one One-Pager, one Fidelity Score and zero or more Verdicts.
- **Audio** — the downloaded audio file of an Episode.
- **Transcript** — the full text of an Episode's Audio.
- **One-Pager** — the LLM-generated one-page synthesis of an Episode: big ideas, actionable items, and other sections as set by the summarize prompt file.
- **Fidelity Score** — an automated assessment, by a different LLM from the one that wrote the One-Pager, of how faithfully the One-Pager reflects the Transcript. Covers accuracy (claims supported by the Transcript) and coverage (major ideas captured), with flagged issues.
- **Verdict** — the user's rating of an Episode, with a reason in free text.
- **Job** — one run of the full pipeline for an Episode.
- **Spend** — the cloud API cost of Jobs, tracked per Episode, step and day.
- **Daily Cap** — the maximum Spend per day, configurable.

## 4. Features
### 4.1 Episode Ingestion Pipeline
**Description:** Submitting a YouTube link starts a Job that runs the full pipeline (Audio, Transcript, One-Pager) in the background. Each step's output is persisted when it finishes. Realizes UJ-1, UJ-3.

#### FR-1: Submit an Episode
Diimrem can submit a YouTube URL and start a Job. Realizes UJ-1.

**Consequences (testable):**
- An invalid or non-YouTube URL is rejected with a message before any Spend.
- A URL already submitted shows the existing Episode instead of creating a duplicate.

#### FR-2: Download Audio
The system downloads the Episode's Audio and stores it.

**Consequences (testable):**
- Audio for a 1 to 3 hour Episode downloads and is stored. 4 to 6 hour Episodes also work.
- A download failure puts the Job in a failed state with the reason shown.

#### FR-3: Transcribe
The system produces a Transcript from the Audio and stores it.

**Consequences (testable):**
- Transcription handles the longest supported Episode length (6 hours).
- The Transcript is stored and viewable.

#### FR-4: Generate the One-Pager
The system generates a One-Pager from the Transcript using the editable summarize prompt and stores it. Realizes UJ-1.

**Consequences (testable):**
- Prompts, including the One-Pager section list, live in editable files, not in code. An edit applies to the next Job.
- Each One-Pager version records which prompts produced it, so a Verdict traces to them.
- Diimrem can regenerate an Episode's One-Pager on request, after seeing the estimated cost and confirming. Nothing regenerates automatically when a prompt changes. Every version is kept.
- Transcripts too long for one model call still produce a single One-Pager. `[ASSUMPTION]`

#### FR-5: Show Job status and recover from failure
The page shows each Job's current step, and failed Jobs can be retried. Realizes UJ-3.

**Consequences (testable):**
- Status is one of: queued, downloading, transcribing, summarizing, verifying, done, failed.
- A retry resumes at the failed step and does not repeat completed steps or their Spend.

#### FR-6: Score One-Pager fidelity
After the One-Pager is generated, the system has a different LLM check it against the Transcript and stores a Fidelity Score. Realizes UJ-1.

**Consequences (testable):**
- The checker model is a different model from the one that generated the One-Pager, and is configurable. `[ASSUMPTION: a different vendor or model family, not just a different prompt]`
- The Fidelity Score reports accuracy and coverage separately, and lists specific unsupported claims and missed major ideas.
- The Fidelity Score is shown with the One-Pager, and low scores are visibly flagged. `[ASSUMPTION: a configurable threshold]`
- This step has its own Spend, status and retry like other steps.
- The score is advisory. It never blocks or rewrites the One-Pager.

**Notes:** `[NOTE FOR PM]` The accuracy check is the easier half, since it verifies claims against the Transcript. The coverage check is weaker, because the checker has to identify the major ideas independently. Verdict reasons remain the ground truth for whether the Fidelity Score can be trusted.

### 4.2 Library
**Description:** A list of past Episodes that opens to the One-Pager, with Transcript, Audio, Verdict and Spend available. Realizes UJ-1, UJ-2.

#### FR-7: Browse Episodes
Diimrem can list all Episodes with title, date, status and Verdict, and can open one. Realizes UJ-1.

**Consequences (testable):**
- Each Episode view shows the One-Pager and Fidelity Score, with links to the Transcript and Audio.
- The library can be searched by text across titles and One-Pagers. `[ASSUMPTION]`

### 4.3 Verdicts
**Description:** After listening or reading, Diimrem records a Verdict. Verdicts are ad hoc, so nothing forces one per Episode. Realizes UJ-2.

#### FR-8: Record a Verdict
Diimrem can record a Verdict on an Episode. Realizes UJ-2.

**Consequences (testable):**
- A Verdict has a worth-it rating and a free-text reason.
- An Episode can carry more than one Verdict over time, and the latest is shown. `[ASSUMPTION]`
- Verdicts are stored with the Episode for later analysis.

### 4.4 Spend Control
**Description:** Every paid step records its cost. A Daily Cap stops new Jobs once reached. Realizes UJ-3.

#### FR-9: Track Spend
The system records the Spend of each step of each Job. Realizes UJ-3.

**Consequences (testable):**
- Each Episode view shows its total Spend and per-step breakdown.
- A page shows today's Spend against the Daily Cap.

#### FR-10: Enforce the Daily Cap
The system refuses to start a new Job when today's Spend has reached the Daily Cap. Realizes UJ-3.

**Consequences (testable):**
- The Daily Cap is configurable and defaults to $5. `[ASSUMPTION: revisit after the first one-hour run]`
- A refused submission tells the user why and leaves no partial Job.
- The system estimates a Job's cost, including the fidelity check, from the Episode's duration before it starts, and refuses if the Job would exceed the cap. `[ASSUMPTION]`

### 4.5 Swappable Providers
**Description:** Transcription and summarization are cloud services today. A local alternative may replace either later without changes to the rest of the app.

#### FR-11: Isolate providers
The pipeline reaches transcription and summarization only through provider interfaces, each configurable.

**Consequences (testable):**
- Changing the configured provider requires no change to the Library, Verdict or Spend features.

## 5. Non-Goals (Explicit)
- Not a product. No accounts, sharing or hosting.
- Not a podcast player or feed manager. It takes links you paste.
- No automatic prediction of whether an Episode is worth it, in v1.
- No non-YouTube sources in v1.

## 6. MVP Scope
### 6.1 In Scope
FR-1 to FR-11: paste-a-link pipeline with fidelity check, library, Verdicts, Spend tracking with Daily Cap, swappable providers.

### 6.2 Out of Scope for MVP
- Predicted scoring. Wait for roughly 20 to 30 Verdicts of data.
- Prompt adaptation from feedback. Prompts stay fixed and hand-editable.
- Local transcription or local LLM implementations. Only the interface is in scope.

## 7. Success Metrics
**Primary**
- **SM-1:** For most Episodes, Diimrem trusts the One-Pager and skips listening, or listens by choice, not because the One-Pager failed. Measured informally through Verdict reasons and by comparing Verdicts against Fidelity Scores. Validates FR-4, FR-6.

**Counter-metrics (do not optimize)**
- **SM-C1:** Number of Episodes processed. Volume is not the goal and adds Spend. Counterbalances SM-1.

**Failure:** the One-Pager misstates or misses the major ideas of the Episode, which forces listening anyway. Both errors count: a wrong claim and a missed main point.

## 8. Open Questions
1. How reliable is the Fidelity Score at catching omissions, compared with false claims? Check against Verdicts once there are enough of them.
2. What does a one-hour run cost? It sets the real Daily Cap.
3. How should the One-Pager handle uncertainty or weak transcript sections?

## 9. Assumptions Index
- FR-4 — long Transcripts are handled internally to still yield one One-Pager.
- FR-7 — text search across the Library.
- FR-8 — multiple Verdicts per Episode, latest shown.
- FR-6 — checker is a different model family; low-score threshold is configurable.
- FR-10 — Daily Cap defaults to $5, pending the one-hour run.
- FR-10 — pre-job cost estimate from duration, including the fidelity check.
