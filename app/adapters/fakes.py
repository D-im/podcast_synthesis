"""Deterministic, zero-cost fake adapters used by the app (config 'fake') and all tests."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from app.ports import (
    Adapters, DownloadResult, Meter, OnePager, Resume, Section, Segment, StepError, Transcript,
    VerificationResult,
)


@dataclass
class FakeBehavior:
    fail_at: str | None = None          # step name that raises StepError
    retryable: bool = True
    message: str = "fake failure"
    unexpected_at: str | None = None    # step name that raises a plain ValueError
    unexpected_message: str = "unexpected"
    partial_write: bool = False         # download writes part of the file, then fails
    cost_micro: int = 0                 # recorded per call via the meter when > 0
    calls: list[str] = field(default_factory=list)

    def enter(self, step: str) -> None:
        self.calls.append(step)
        if self.unexpected_at == step:
            raise ValueError(self.unexpected_message)

    def charge(self, meter: Meter) -> None:
        if self.cost_micro > 0:
            meter.record("fake", self.cost_micro)

    def maybe_fail(self, step: str) -> None:
        if self.fail_at == step:
            raise StepError(self.message, self.retryable)


class FakeDownloader:
    def __init__(self, b: FakeBehavior):
        self.b = b

    def download(self, video_id: str, url: str, dest: Path, meter: Meter) -> DownloadResult:
        self.b.enter("download")
        self.b.charge(meter)
        if self.b.partial_write:
            Path(dest).write_bytes(b"partial")
            raise StepError(self.b.message, self.b.retryable)
        self.b.maybe_fail("download")
        Path(dest).write_bytes(f"fake-audio:{video_id}".encode())
        return DownloadResult(title=f"Fake episode {video_id}", duration_seconds=3600)


class FakeTranscriber:
    def __init__(self, b: FakeBehavior):
        self.b = b

    def transcribe(self, audio_path: Path, meter: Meter,
                   resume: Resume | None = None) -> Transcript:
        self.b.enter("transcribe")
        self.b.charge(meter)
        self.b.maybe_fail("transcribe")
        return Transcript((
            Segment(0.0, 5.0, "Welcome to the fake show."),
            Segment(5.0, 12.0, "Today we discuss deterministic testing."),
            Segment(12.0, 20.0, "Thanks for listening."),
        ))


class FakeSummarizer:
    def __init__(self, b: FakeBehavior):
        self.b = b

    def summarize(self, transcript: Transcript, meter: Meter) -> OnePager:
        self.b.enter("summarize")
        self.b.charge(meter)
        self.b.maybe_fail("summarize")
        return OnePager((
            Section("Summary", f"Fake summary of {len(transcript.segments)} segments."),
            Section("Key ideas", "Deterministic testing matters."),
        ))


class FakeVerifier:
    def __init__(self, b: FakeBehavior):
        self.b = b

    def verify(self, transcript: Transcript, one_pager: OnePager,
               meter: Meter) -> VerificationResult:
        self.b.enter("verify")
        self.b.charge(meter)
        self.b.maybe_fail("verify")
        return VerificationResult(1.0, 1.0, (), ())


def build_fakes(behavior: FakeBehavior | None = None, verifier: bool = True) -> Adapters:
    b = behavior or FakeBehavior()
    return Adapters(
        FakeDownloader(b), FakeTranscriber(b), FakeSummarizer(b),
        FakeVerifier(b) if verifier else None,
    )
