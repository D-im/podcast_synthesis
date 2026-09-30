"""Split a Transcript into ordered chunks at segment boundaries, for the map step."""
from __future__ import annotations

import math
from dataclasses import dataclass

from app.core.text import segment_line
from app.ports import Transcript


@dataclass(frozen=True)
class Chunk:
    text: str       # formatted lines, same format as the single-call path
    start: float    # time range in seconds
    end: float


def estimate_tokens(text: str) -> int:
    """Deliberately conservative: one token per three characters."""
    return math.ceil(len(text) / 3)


def split_transcript(transcript: Transcript, max_tokens: int) -> list[Chunk]:
    """Keep segments in order, never split one, and start a new chunk when the next segment
    would push the chunk's estimate above `max_tokens`. An oversized segment stands alone."""
    chunks: list[Chunk] = []
    lines: list[str] = []
    size = 0            # characters in the chunk text so far, newlines included
    start = end = 0.0

    def flush() -> None:
        nonlocal lines, size
        if lines:
            chunks.append(Chunk("\n".join(lines), start, end))
        lines, size = [], 0

    for seg in transcript.segments:
        line = segment_line(seg)
        added = len(line) + (1 if lines else 0)
        if lines and estimate_tokens("x" * (size + added)) > max_tokens:
            flush()
            added = len(line)
        if not lines:
            start, end = seg.start, seg.end
        else:
            end = max(end, seg.end)
        lines.append(line)
        size += added
    flush()
    return chunks
