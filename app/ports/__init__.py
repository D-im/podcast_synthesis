"""Provider boundary: result types, errors and the four ports."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol


class StepError(Exception):
    """Expected failure of a step. The message must never contain secrets or transcript text."""

    def __init__(self, message: str, retryable: bool = True):
        super().__init__(message)
        self.message = message
        self.retryable = retryable


class StepSkipped(Exception):
    """A step chose not to run (for example input too long). The pipeline records it as
    `skipped` with this reason, and the Job carries on. The reason must hold no secrets or text."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class DownloadResult:
    title: str
    duration_seconds: int


@dataclass(frozen=True)
class Segment:
    start: float
    end: float
    text: str
    speaker: str | None = None


@dataclass(frozen=True)
class Transcript:
    segments: tuple[Segment, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"segments": [asdict(s) for s in self.segments]}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Transcript":
        return cls(tuple(Segment(s["start"], s["end"], s["text"], s.get("speaker")) for s in d["segments"]))

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
    model: str = ""
    prompt_hashes: dict[str, str] = field(default_factory=dict)  # prompt name -> SHA-256

    def to_dict(self) -> dict[str, Any]:
        return {
            "sections": [asdict(s) for s in self.sections],
            "model": self.model,
            "prompt_hashes": dict(self.prompt_hashes),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "OnePager":
        return cls(
            tuple(Section(s["name"], s["text"]) for s in d["sections"]),
            model=d.get("model", ""),
            prompt_hashes=dict(d.get("prompt_hashes") or {}),
        )


@dataclass(frozen=True)
class Claim:
    section: str
    claim: str
    verdict: str          # "supported" or "unsupported" (after the evidence check)
    evidence: str
    note: str
    evidence_found: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class UnsupportedClaim:
    section: str
    claim: str
    note: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class IdeaCheck:
    """One main idea of the Transcript and whether the One-Pager conveys it."""
    title: str
    description: str
    coverage: str         # "covered", "partial" or "missing"
    where: str
    note: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MissedIdea:
    title: str
    coverage: str         # "partial" or "missing"
    note: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class VerificationResult:
    accuracy: float
    coverage: float | None = None   # None when no coverage check ran
    unsupported_claims: tuple[UnsupportedClaim, ...] = ()
    missed_ideas: tuple[MissedIdea, ...] = ()
    ideas: tuple[IdeaCheck, ...] = ()
    claims: tuple[Claim, ...] = ()
    model: str = ""
    prompt_hashes: dict[str, str] = field(default_factory=dict)  # prompt name -> SHA-256

    def to_dict(self) -> dict[str, Any]:
        return {
            "accuracy": self.accuracy,
            "coverage": self.coverage,
            "claims": [c.to_dict() for c in self.claims],
            "unsupported_claims": [
                u.to_dict() if hasattr(u, "to_dict") else u for u in self.unsupported_claims],
            "ideas": [i.to_dict() for i in self.ideas],
            "missed_ideas": [m.to_dict() if hasattr(m, "to_dict") else m
                             for m in self.missed_ideas],
            "model": self.model,
            "prompt_hashes": dict(self.prompt_hashes),
        }


class Meter(Protocol):
    """Records one paid call. Adapters must call it for every paid call they make.

    Record as soon as a call has been billed, before parsing or validating its response,
    so a cost is never lost when the step fails afterwards. The ledger cannot be edited.
    """

    def record(self, provider: str, amount_micro_usd: int, ref: str | None = None) -> None:
        """`ref` (e.g. a vendor job ID) makes the row idempotent: a repeat is ignored."""
        ...


class Resume(Protocol):
    """Step-bound handle for a vendor job ID, so a re-run resumes instead of paying twice."""

    def load(self) -> str | None: ...

    def save(self, vendor_job_id: str) -> None: ...

    def clear(self) -> None: ...


class Downloader(Protocol):
    def download(self, video_id: str, url: str, dest: Path, meter: Meter) -> DownloadResult: ...


class Transcriber(Protocol):
    def transcribe(self, audio_path: Path, meter: Meter, resume: Resume) -> Transcript: ...


class NotesCache(Protocol):
    """Finished map-step notes or verifier passes, so a retry never pays for them twice."""

    def get(self, key: str) -> str | None: ...

    def put(self, key: str, text: str) -> None: ...


class Summarizer(Protocol):
    def summarize(self, transcript: Transcript, meter: Meter, cache: NotesCache) -> OnePager: ...


class Verifier(Protocol):
    def verify(
        self, transcript: Transcript, one_pager: OnePager, meter: Meter,
        cache: NotesCache | None = None,
    ) -> VerificationResult: ...


@dataclass(frozen=True)
class Adapters:
    downloader: Downloader
    transcriber: Transcriber
    summarizer: Summarizer
    verifier: Verifier | None
