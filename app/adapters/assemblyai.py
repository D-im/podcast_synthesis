"""AssemblyAI adapter for the Transcriber port.

The audio goes up as one file. The vendor job ID is saved before waiting, so a re-run polls the
same job instead of paying again. Only `SdkClient` touches the SDK; tests replace it.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Callable, Protocol

from app.core.meter import to_micro
from app.ports import Meter, Resume, Segment, StepError, Transcript

log = logging.getLogger(__name__)

PROVIDER = "assemblyai"
KEY_VAR = "ASSEMBLYAI_API_KEY"
MAX_TRANSIENT_RETRIES = 5
MAX_BACKOFF_SECONDS = 60.0


class AuthRejected(Exception):
    """The vendor rejected the API key."""


class Transient(Exception):
    """Network trouble, rate limit or vendor 5xx. Worth retrying."""


class JobGone(Exception):
    """The vendor does not know the saved job ID."""


class Refused(Exception):
    """The vendor refused the request for a reason retrying will not fix."""


@dataclass(frozen=True)
class JobStatus:
    state: str              # queued | processing | completed | error
    error: str | None = None
    audio_duration: float | None = None   # seconds, when the vendor reports it


class Client(Protocol):
    def submit(self, audio_path: Path) -> str: ...

    def status(self, job_id: str) -> JobStatus: ...

    def utterances(self, job_id: str) -> list[Segment]: ...

    def sentences(self, job_id: str) -> list[Segment]: ...


_URL = re.compile(r"https?://\S+")


def _describe(exc: BaseException, limit: int = 200) -> str:
    """Short, URL-free text for an error, so the real cause is visible in the step message."""
    # only the vendor SDK's and the HTTP library's own messages are shown; they carry the
    # reason (for example a rejected setting) and never the key. Anything else: class name only.
    shown = type(exc).__module__.split(".")[0] in ("assemblyai", "httpx", "httpcore")
    text = " ".join(_URL.sub("<url>", str(exc)).split()) if shown else ""
    key = os.environ.get(KEY_VAR)
    if key and text:
        text = text.replace(key, "[redacted]")
    text = f"{type(exc).__name__}: {text}" if text else type(exc).__name__
    return text if len(text) <= limit else text[: limit - 1] + "…"


class _HttpError(Exception):
    def __init__(self, status_code: int):
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code


def _seg(start_ms: float, end_ms: float, text: str, speaker: str | None) -> Segment:
    return Segment(start_ms / 1000.0, end_ms / 1000.0, text, speaker or None)


class SdkClient:
    """Thin wrapper over the assemblyai SDK. Raises only AuthRejected, Transient, JobGone or
    Refused, with messages that never contain the key."""

    def __init__(self, api_key: str, speech_models: list[str], speaker_labels: bool):
        import assemblyai as aai

        self._aai = aai
        self._client = aai.Client(api_key=api_key)
        self._config = aai.TranscriptionConfig(
            speech_models=speech_models, speaker_labels=speaker_labels
        )

    @staticmethod
    def _map(exc: Exception) -> Exception:
        import httpx

        code = getattr(exc, "status_code", None)
        if isinstance(exc, httpx.HTTPStatusError):
            code = exc.response.status_code
        if code in (401, 403):
            return AuthRejected()
        if code == 404:
            return JobGone()
        if code in (408, 425, 429):
            return Transient(f"HTTP {code}")
        if isinstance(code, int) and 400 <= code < 500:
            return Refused(f"HTTP {code}")
        return Transient(_describe(exc))

    def submit(self, audio_path: Path) -> str:
        try:
            t = self._aai.Transcriber(client=self._client).submit(str(audio_path), self._config)
        except Exception as e:
            raise self._map(e) from None
        if getattr(t, "status", None) == self._aai.TranscriptStatus.error:
            raise Refused(str(getattr(t, "error", None) or "the vendor rejected the audio"))
        if not t.id:
            raise Transient("no job id returned")
        return t.id

    def status(self, job_id: str) -> JobStatus:
        import httpx

        try:
            r = self._client.http_client.get(f"/v2/transcript/{job_id}")
            if r.status_code != 200:
                raise _HttpError(r.status_code)
            body = r.json()
            if not isinstance(body, dict):
                raise ValueError("unexpected response shape")
        except Exception as e:
            if isinstance(e, (httpx.HTTPError, ValueError, _HttpError)):
                mapped = self._map(e)
                # an unexpected 4xx while polling is not worth a permanent failure
                raise Transient(str(mapped)) if isinstance(mapped, Refused) else mapped from None
            raise
        duration = body.get("audio_duration")
        return JobStatus(
            state=str(body.get("status")),
            error=body.get("error"),
            audio_duration=float(duration) if isinstance(duration, (int, float)) else None,
        )

    def _finished(self, job_id: str):
        # the job is already complete, so this returns after one request
        return self._aai.Transcript(job_id, client=self._client).wait_for_completion()

    def utterances(self, job_id: str) -> list[Segment]:
        try:
            t = self._finished(job_id)
            return [_seg(u.start, u.end, u.text, u.speaker) for u in (t.utterances or [])]
        except Exception as e:
            raise self._map(e) from None

    def sentences(self, job_id: str) -> list[Segment]:
        try:
            t = self._finished(job_id)
            return [_seg(s.start, s.end, s.text, None) for s in t.get_sentences()]
        except Exception as e:
            raise self._map(e) from None


def cost_micro_usd(duration_seconds: float, usd_per_hour: float) -> int:
    """Duration times hourly price in micro-dollars, rounded half up."""
    usd = Decimal(str(duration_seconds)) * Decimal(str(usd_per_hour)) / Decimal(3600)
    return to_micro(usd)


def ffmpeg_compress(src: Path, dest: Path, bitrate_kbps: int) -> None:
    """Re-encode to mono 16 kHz AAC, which is plenty for speech and far smaller to upload."""
    exe = shutil.which("ffmpeg")
    if not exe:
        raise FileNotFoundError("ffmpeg")
    try:
        subprocess.run(
            [exe, "-y", "-loglevel", "error", "-i", str(src), "-vn", "-ac", "1", "-ar", "16000",
             "-c:a", "aac", "-b:a", f"{bitrate_kbps}k", str(dest)],
            check=True, capture_output=True, timeout=600,
        )
    except subprocess.CalledProcessError as e:
        tail = (e.stderr or b"").decode(errors="replace").strip()[-200:]
        raise RuntimeError(f"ffmpeg exited with {e.returncode}: {tail}") from None


def _rejected() -> StepError:
    return StepError(f"AssemblyAI rejected the API key; check {KEY_VAR}", True)


class AssemblyAITranscriber:
    def __init__(
        self,
        speech_models: list[str],
        speaker_labels: bool = True,
        usd_per_hour: float = 0.23,
        poll_interval_seconds: float = 5.0,
        max_wait_minutes: float = 180.0,
        compress_before_upload: bool = True,
        upload_bitrate_kbps: int = 32,
        compressor: Callable[[Path, Path, int], None] | None = None,
        client_factory: Callable[[str], Client] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        environ=None,
    ):
        self.speaker_labels = speaker_labels
        self.usd_per_hour = usd_per_hour
        self.poll_interval = poll_interval_seconds
        self.max_wait_seconds = max_wait_minutes * 60
        self.client_factory = client_factory or (
            lambda key: SdkClient(key, speech_models, speaker_labels)
        )
        self.compress_before_upload = compress_before_upload
        self.upload_bitrate_kbps = upload_bitrate_kbps
        self.compressor = compressor or ffmpeg_compress
        self.sleep = sleep
        self.clock = clock
        self.environ = os.environ if environ is None else environ

    @classmethod
    def from_config(cls, config) -> "AssemblyAITranscriber":
        return cls(
            speech_models=[config.models["transcriber"]],
            speaker_labels=config.speaker_labels,
            usd_per_hour=config.transcription_usd_per_hour,
            poll_interval_seconds=config.poll_interval_seconds,
            max_wait_minutes=config.max_wait_minutes,
            compress_before_upload=config.compress_before_upload,
            upload_bitrate_kbps=config.upload_bitrate_kbps,
        )

    def transcribe(self, audio_path: Path, meter: Meter, resume: Resume) -> Transcript:
        key = self.environ.get(KEY_VAR)
        if not key:
            raise StepError(f"{KEY_VAR} is not set; add it to the environment or .env", True)
        client = self.client_factory(key)

        job_id = resume.load()
        if not job_id:
            job_id = self._upload(client, audio_path)
            resume.save(job_id)
        status = self._wait(client, job_id, resume)

        try:
            segments = client.utterances(job_id) if self.speaker_labels else []
            if not segments:  # no speaker turns (single voice or labels off): use sentences
                segments = client.sentences(job_id)
        except AuthRejected:
            raise _rejected() from None
        except JobGone:
            resume.clear()
            raise StepError(
                "AssemblyAI no longer has the finished transcription job; "
                "retry to submit again", True) from None
        except Exception as e:
            raise StepError(
                f"could not fetch the finished transcript ({type(e).__name__}); "
                "retrying will not resubmit the audio", True) from None
        duration = status.audio_duration
        if not duration:
            duration = segments[-1].end if segments else 0.0
        # record before building the Transcript so a paid job is never lost
        meter.record(PROVIDER, cost_micro_usd(duration, self.usd_per_hour), ref=job_id)
        if not segments:
            raise StepError("AssemblyAI returned no speech for this audio", False)
        return Transcript(tuple(segments))

    def _upload(self, client: Client, audio_path: Path) -> str:
        """Send the audio, re-encoded smaller when possible. The original file is never touched."""
        if not self.compress_before_upload:
            return self._submit(client, audio_path)
        small = audio_path.with_name(f".upload-{audio_path.stem}.m4a")
        try:
            try:
                self.compressor(audio_path, small, self.upload_bitrate_kbps)
                usable = small.is_file() and 0 < small.stat().st_size < audio_path.stat().st_size
                send = small if usable else audio_path
            except Exception as e:  # an optimisation only: fall back to the original audio
                log.warning("audio compression failed, uploading the original (%s)",
                            _describe(e) if not isinstance(e, RuntimeError) else str(e)[:300])
                send = audio_path
            return self._submit(client, send)
        finally:
            try:
                small.unlink()
            except OSError:
                pass

    def _submit(self, client: Client, audio_path: Path) -> str:
        try:
            return client.submit(audio_path)
        except AuthRejected:
            raise _rejected() from None
        except Transient as e:
            raise StepError(f"could not submit audio to AssemblyAI ({e})", True) from None
        except Refused as e:
            raise StepError(f"AssemblyAI refused the audio ({e})", False) from None
        except Exception as e:
            raise StepError(f"could not submit audio to AssemblyAI ({_describe(e)})",
                            True) from None

    def _wait(self, client: Client, job_id: str, resume: Resume) -> JobStatus:
        deadline = self.clock() + self.max_wait_seconds
        failures = 0
        while True:
            try:
                status = client.status(job_id)
            except AuthRejected:
                raise _rejected() from None
            except JobGone:
                resume.clear()
                raise StepError(
                    "AssemblyAI no longer has the saved transcription job; "
                    "retry to submit again", True) from None
            except Transient as e:
                failures += 1
                if failures > MAX_TRANSIENT_RETRIES:
                    raise StepError(
                        f"AssemblyAI kept failing while polling job {job_id} ({e}); "
                        "retrying will resume it", True) from None
                self.sleep(min(self.poll_interval * 2 ** (failures - 1), MAX_BACKOFF_SECONDS))
            else:
                if status.state not in ("queued", "processing", "completed", "error"):
                    # an unknown state is treated like a transient fault, not polled for hours
                    failures += 1
                    if failures > MAX_TRANSIENT_RETRIES:
                        raise StepError(
                            f"AssemblyAI reported an unknown state for job {job_id}; "
                            "retrying will resume it", True)
                    self.sleep(min(self.poll_interval * 2 ** (failures - 1), MAX_BACKOFF_SECONDS))
                    if self.clock() >= deadline:
                        raise StepError(
                            f"transcription job {job_id} did not finish within "
                            f"{self.max_wait_seconds / 60:g} minutes; retrying will resume it",
                            True)
                    continue
                failures = 0
                if status.state == "completed":
                    return status
                if status.state == "error":
                    resume.clear()
                    raise StepError(
                        "AssemblyAI could not transcribe this audio: "
                        f"{status.error or 'unknown error'}", False)
                self.sleep(self.poll_interval)
            if self.clock() >= deadline:
                raise StepError(
                    f"transcription job {job_id} did not finish within "
                    f"{self.max_wait_seconds / 60:g} minutes; retrying will resume it", True)
