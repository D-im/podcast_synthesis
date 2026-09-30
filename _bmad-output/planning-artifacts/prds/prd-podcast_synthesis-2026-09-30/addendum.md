# Addendum: podcast_synthesis

Technical notes from discovery, kept out of the PRD.

- Storage: SQLite plus local files for Audio, Transcript and One-Pager.
- Execution: background jobs with persisted per-step state, so retries resume.
- Providers: cloud transcription and LLM now; local Whisper and local LLM later behind the same interfaces.
- Long inputs: 6-hour Episodes will exceed a single LLM context, so expect chunking or map-reduce summarization. Architecture decision.
- Verdict data is the future raw material for a worth-it scoring model.
- Fidelity check: second-model verification likely needs claim extraction then transcript retrieval for long Episodes; architecture decision.
