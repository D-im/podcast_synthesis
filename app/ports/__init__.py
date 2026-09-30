"""Provider boundary: result types, errors and the four ports."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol


class StepError(Exception):
    """Expected failure of a step. The message must never contain secrets or transcript text."""

    def __init__(self, message: str, retryable: bool = True):
        super().__init__(message)
        self.message = message
        self.retryable = retryable


@dataclass(frozen=True)
class DownloadResult:
    title: str
    duration_seconds: int


@dataclass(frozen=True)
class Segment:
    start: float
    end: float
    text: str


@dataclass(frozen=True)
class Transcript:
    segments: tuple[Segment, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"segments": [asdict(s) for s in self.segments]}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Transcript":
        return cls(tuple(Segment(s["start"], s["end"], s["text"]) for s in d["segments"]))

    @property
    def text(self) -> str:
        return " ".join(s.text for s in self.segments)


@dataclass(frozen=True)
class Section:
    name: str
    text: str


@dataclass(frozen=True)
class OnePager:
    sections: tuple[Section, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"sections": [asdict(s) for s in self.sections]}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "OnePager":
        return cls(tuple(Section(s["name"], s["text"]) for s in d["sections"]))


@dataclass(frozen=True)
class VerificationResult:
    accuracy: float
    coverage: float
    unsupported_claims: tuple[str, ...]
    missed_ideas: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "accuracy": self.accuracy,
            "coverage": self.coverage,
            "unsupported_claims": list(self.unsupported_claims),
            "missed_ideas": list(self.missed_ideas),
        }


class Meter(Protocol):
    """Records one paid call. Adapters must call it for every paid call they make.

    Record as soon as a call has been billed, before parsing or validating its response,
    so a cost is never lost when the step fails afterwards. The ledger cannot be edited.
    """

    def record(self, provider: str, amount_micro_usd: int) -> None: ...


class Downloader(Protocol):
    def download(self, video_id: str, url: str, dest: Path, meter: Meter) -> DownloadResult: ...


class Transcriber(Protocol):
    def transcribe(self, audio_path: Path, meter: Meter) -> Transcript: ...


class Summarizer(Protocol):
    def summarize(self, transcript: Transcript, meter: Meter) -> OnePager: ...


class Verifier(Protocol):
    def verify(
        self, transcript: Transcript, one_pager: OnePager, meter: Meter
    ) -> VerificationResult: ...


@dataclass(frozen=True)
class Adapters:
    downloader: Downloader
    transcriber: Transcriber
    summarizer: Summarizer
    verifier: Verifier | None
