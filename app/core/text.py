"""Text helpers shared by the web layer and adapters."""
from __future__ import annotations

from app.ports import Segment, Transcript


def format_timestamp(seconds: float, always_hours: bool = False) -> str:
    """mm:ss, or h:mm:ss from one hour on (or always, with `always_hours`)."""
    total = max(0, int(seconds))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h or always_hours else f"{m:02d}:{s:02d}"


def segment_line(s: Segment) -> str:
    """`[h:mm:ss] Speaker: text`, the speaker part only when present."""
    stamp = format_timestamp(s.start, always_hours=True)
    who = f"{s.speaker}: " if s.speaker else ""
    return f"[{stamp}] {who}{s.text}"


def transcript_text(transcript: Transcript) -> str:
    """One line per segment, see `segment_line`."""
    return "\n".join(segment_line(s) for s in transcript.segments)
