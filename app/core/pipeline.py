"""The step state machine: run one Job through its ordered steps."""
from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterable
from pathlib import Path

from app.core.submit import STEP_NAMES
from app.ports import Adapters, OnePager, StepError, Transcript
from app.store import artifacts, episodes

MAX_MESSAGE = 500
NO_VERIFIER_REASON = "no verifier configured"

ARTIFACT_FOR_STEP = {
    "download": artifacts.AUDIO,
    "transcribe": artifacts.TRANSCRIPT,
    "summarize": artifacts.ONE_PAGER,
    "verify": artifacts.VERIFICATION,
}


def sanitize(message: str, secrets: Iterable[str]) -> str:
    """Redact secret values first, then truncate (so a cut can't leave a partial secret)."""
    for s in secrets:
        if s:
            message = message.replace(s, "[redacted]")
    return message[:MAX_MESSAGE]


def run_job(
    conn: sqlite3.Connection,
    data_dir: Path,
    job: dict,
    adapters: Adapters,
    secrets: Callable[[], Iterable[str]] = lambda: (),
) -> str:
    """Run a Job to completion or first failure. Returns the final Job state."""
    job_id, video_id = job["id"], job["video_id"]
    path = lambda step: artifacts.artifact_path(data_dir, video_id, ARTIFACT_FOR_STEP[step])
    states = {s["name"]: s["state"] for s in episodes.get_steps(conn, job_id)}
    episodes.set_job_state(conn, job_id, "running")

    def run_step(name: str) -> None:
        final = path(name)
        if name == "download":
            with artifacts.atomic_target(final) as tmp:
                result = adapters.downloader.download(video_id, job["url"], tmp)
                if not artifacts.exists(tmp):
                    raise StepError("download produced no audio file", True)
            episodes.set_episode_metadata(
                conn, video_id, result.title, result.duration_seconds
            )
        elif name == "transcribe":
            artifacts.write_json(
                final, adapters.transcriber.transcribe(path("download")).to_dict()
            )
        elif name == "summarize":
            transcript = Transcript.from_dict(artifacts.read_json(path("transcribe")))
            artifacts.write_json(final, adapters.summarizer.summarize(transcript).to_dict())
        elif name == "verify":
            transcript = Transcript.from_dict(artifacts.read_json(path("transcribe")))
            one_pager = OnePager.from_dict(artifacts.read_json(path("summarize")))
            artifacts.write_json(
                final, adapters.verifier.verify(transcript, one_pager).to_dict()
            )
        else:
            raise RuntimeError(f"unknown step {name}")

    rerun_rest = False  # once a step really runs, later steps must not reuse old output
    for name in STEP_NAMES:
        if name not in states:
            continue
        if not rerun_rest and states[name] == "done" and artifacts.exists(path(name)):
            continue
        rerun_rest = True
        if name == "verify" and adapters.verifier is None:
            episodes.set_step_state(conn, job_id, name, "skipped", NO_VERIFIER_REASON)
            continue
        episodes.set_step_state(conn, job_id, name, "running")
        try:
            run_step(name)
        except StepError as e:
            message, retryable = sanitize(e.message, secrets()), e.retryable
        except Exception as e:  # unexpected: class name only, never the text
            message, retryable = type(e).__name__, True
        else:
            episodes.set_step_state(conn, job_id, name, "done")
            continue
        episodes.set_step_state(conn, job_id, name, "failed", message, retryable)
        episodes.set_job_state(conn, job_id, "failed")
        return "failed"

    episodes.set_job_state(conn, job_id, "done")
    return "done"
