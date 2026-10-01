"""The step state machine: run one Job through its ordered steps."""
from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterable
from pathlib import Path

from app.core import guard
from app.core.meter import StepMeter
from app.core.submit import STEP_NAMES
from app.ports import Adapters, OnePager, StepError, StepSkipped, Transcript
from app.store import artifacts, episodes

MAX_MESSAGE = 500
NO_VERIFIER_REASON = "no verifier configured"

ARTIFACT_FOR_STEP = {
    "download": artifacts.AUDIO,
    "transcribe": artifacts.TRANSCRIPT,
    "summarize": artifacts.ONE_PAGER,
}  # verify has no single file: its result is verification.v<N>.json per One-Pager version


class StepResume:
    """Resume handle bound to one step, backed by steps.vendor_job_id."""

    def __init__(self, conn: sqlite3.Connection, job_id: int, step: str):
        self.conn, self.job_id, self.step = conn, job_id, step

    def load(self) -> str | None:
        return episodes.get_vendor_job_id(self.conn, self.job_id, self.step)

    def save(self, vendor_job_id: str) -> None:
        episodes.set_vendor_job_id(self.conn, self.job_id, self.step, vendor_job_id)

    def clear(self) -> None:
        episodes.set_vendor_job_id(self.conn, self.job_id, self.step, None)


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
    config_path: Path | None = None,
) -> str:
    """Run a Job to completion, first failure, or a Daily Cap pause. Returns the Job state.

    With `config_path`, every paid step first goes through `guard.check_step`; over the cap the
    Job is `paused` at that step boundary with every artifact kept.
    """
    job_id, video_id = job["id"], job["video_id"]
    path = lambda step: artifacts.artifact_path(data_dir, video_id, ARTIFACT_FOR_STEP[step])
    def finished(step: str) -> bool:
        if step == "summarize":  # done only while the latest version file exists
            latest = episodes.latest_one_pager_version(conn, video_id)
            return latest is not None and artifacts.exists(
                artifacts.one_pager_path(data_dir, video_id, latest["version"]))
        if step == "verify":  # done only while the latest One-Pager version has its result
            latest = episodes.latest_one_pager_version(conn, video_id)
            score = episodes.latest_fidelity_score(conn, video_id)
            return (latest is not None and artifacts.exists(
                artifacts.verification_path(data_dir, video_id, latest["version"]))
                and score is not None and score["version"] == latest["version"])
        return artifacts.exists(path(step))

    states = {s["name"]: s["state"] for s in episodes.get_steps(conn, job_id)}
    episodes.set_job_state(conn, job_id, "running")

    def run_step(name: str) -> None:
        final = path(name) if name in ARTIFACT_FOR_STEP else None
        meter = StepMeter(conn, video_id, name)
        if name == "download":
            with artifacts.atomic_target(final) as tmp:
                result = adapters.downloader.download(video_id, job["url"], tmp, meter)
                if not artifacts.exists(tmp):
                    raise StepError("download produced no audio file", True)
            episodes.set_episode_metadata(
                conn, video_id, result.title, result.duration_seconds
            )
        elif name == "transcribe":
            resume = StepResume(conn, job_id, name)
            artifacts.write_json(
                final,
                adapters.transcriber.transcribe(path("download"), meter, resume).to_dict(),
            )
        elif name == "summarize":
            transcript = Transcript.from_dict(artifacts.read_json(path("transcribe")))
            one_pager = adapters.summarizer.summarize(
                transcript, meter, artifacts.notes_cache(data_dir, video_id))
            latest = episodes.latest_one_pager_version(conn, video_id)
            version = max(latest["version"] if latest else 0,
                          artifacts.latest_one_pager_file_version(data_dir, video_id)) + 1
            # file first, row second: a crash between leaves a stray file, never a row without one
            artifacts.write_json(artifacts.one_pager_path(data_dir, video_id, version),
                                 one_pager.to_dict())
            episodes.add_one_pager_version(
                conn, video_id, version, one_pager.model, one_pager.prompt_hashes)
        elif name == "verify":
            transcript = Transcript.from_dict(artifacts.read_json(path("transcribe")))
            latest = episodes.latest_one_pager_version(conn, video_id)
            if latest is None:
                raise StepError("no One-Pager to verify", True)
            latest_path = artifacts.one_pager_path(data_dir, video_id, latest["version"])
            if not artifacts.exists(latest_path):
                raise StepError("the latest One-Pager file is missing; run summarize again", True)
            one_pager = OnePager.from_dict(artifacts.read_json(latest_path))
            result = adapters.verifier.verify(
                transcript, one_pager, meter, artifacts.verify_cache(data_dir, video_id))
            version = latest["version"]
            # file first, row second, like the One-Pager; earlier versions' files stay as they are
            artifacts.write_json(
                artifacts.verification_path(data_dir, video_id, version),
                {"one_pager_version": version, **result.to_dict()})
            episodes.add_fidelity_score(
                conn, video_id, version, result.model, result.prompt_hashes, result.accuracy,
                result.coverage)
        else:
            raise RuntimeError(f"unknown step {name}")

    rerun_rest = False  # once a step really runs, later steps must not reuse old output
    for name in STEP_NAMES:
        if name not in states:
            continue
        if not rerun_rest and states[name] == "done" and finished(name):
            continue
        rerun_rest = True
        if name == "verify" and adapters.verifier is None:
            episodes.set_step_state(conn, job_id, name, "skipped", NO_VERIFIER_REASON)
            continue
        if config_path is not None and name in guard.PAID_STEPS:
            try:
                decision = guard.check_step(conn, data_dir, video_id, name, config_path)
            except StepError as e:
                episodes.set_step_state(conn, job_id, name, "failed",
                                        sanitize(e.message, secrets()), e.retryable)
                episodes.set_job_state(conn, job_id, "failed")
                return "failed"
            if not decision.allowed:
                episodes.pause_job(conn, job_id, guard.pause_reason(name, decision))
                return "paused"
        episodes.set_step_state(conn, job_id, name, "running")
        try:
            run_step(name)
        except StepSkipped as e:
            episodes.set_step_state(
                conn, job_id, name, "skipped", sanitize(e.reason, secrets()))
            continue
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
