"""Episode artifact files: atomic writes under data/episodes/<video_id>/."""
from __future__ import annotations

import json
import os
import re
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

AUDIO = "audio.m4a"
TRANSCRIPT = "transcript.json"
ONE_PAGER = "one_pager.json"
VERIFICATION = "verification.json"


_SAFE_ID = re.compile(r"[A-Za-z0-9_-]+")


def episode_dir(data_dir: Path, video_id: str) -> Path:
    if not _SAFE_ID.fullmatch(video_id):
        raise ValueError("unsafe video id for an artifact path")
    return Path(data_dir) / "episodes" / video_id


def artifact_path(data_dir: Path, video_id: str, name: str) -> Path:
    return episode_dir(data_dir, video_id) / name


def exists(path: Path) -> bool:
    return Path(path).is_file()


def temp_path(final: Path) -> Path:
    final = Path(final)
    return final.with_name(final.name + ".tmp")


def cleanup_temp(final: Path) -> None:
    try:
        temp_path(final).unlink()
    except FileNotFoundError:
        pass


def promote(final: Path) -> None:
    """Flush the finished temp file to disk and atomically move it to its final name."""
    final = Path(final)
    tmp = temp_path(final)
    fd = os.open(tmp, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, final)


@contextmanager
def atomic_target(final: Path) -> Iterator[Path]:
    """Yield a temp path to write to; promote on success, remove the temp on any failure."""
    final = Path(final)
    final.parent.mkdir(parents=True, exist_ok=True)
    cleanup_temp(final)
    try:
        yield temp_path(final)
        promote(final)
    except BaseException:
        cleanup_temp(final)
        raise


def write_json(final: Path, obj: Any) -> None:
    with atomic_target(final) as tmp:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f)
            f.flush()
            os.fsync(f.fileno())


def read_json(path: Path) -> Any:
    with open(path, encoding="utf-8") as f:
        return json.load(f)
