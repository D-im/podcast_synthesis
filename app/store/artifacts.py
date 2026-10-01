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
ONE_PAGER = "one_pager.json"  # legacy unversioned name; new runs write one_pager.v<N>.json


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


def verification_path(data_dir: Path, video_id: str, version: int) -> Path:
    """The check result for One-Pager version N, next to that version's file."""
    return artifact_path(data_dir, video_id, f"verification.v{int(version)}.json")


_ONE_PAGER_FILE = re.compile(r"one_pager\.v(\d+)\.json")


def one_pager_path(data_dir: Path, video_id: str, version: int) -> Path:
    return artifact_path(data_dir, video_id, f"one_pager.v{int(version)}.json")


def latest_one_pager_file_version(data_dir: Path, video_id: str) -> int:
    """Highest One-Pager version file on disk, or 0. Guards against overwriting a stray file."""
    try:
        names = os.listdir(episode_dir(data_dir, video_id))
    except FileNotFoundError:
        return 0
    return max((int(m.group(1)) for n in names if (m := _ONE_PAGER_FILE.fullmatch(n))),
               default=0)


_NOTES_KEY = re.compile(r"[0-9a-f]{64}")
NOTES_DIR = "summarize-notes"


class FileNotesCache:
    """Map-step notes as one file per key under an episode's `summarize-notes/` folder."""

    def __init__(self, folder: Path):
        self.folder = Path(folder)

    def _path(self, key: str) -> Path:
        if not _NOTES_KEY.fullmatch(key):
            raise ValueError("unsafe notes cache key")
        return self.folder / f"{key}.txt"

    def get(self, key: str) -> str | None:
        try:
            text = self._path(key).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):  # missing, unreadable or corrupt: just re-make it
            return None
        return text if text.strip() else None

    def put(self, key: str, text: str) -> None:
        final = self._path(key)
        with atomic_target(final) as tmp:
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(text)
                f.flush()
                os.fsync(f.fileno())


def notes_cache(data_dir: Path, video_id: str) -> FileNotesCache:
    return FileNotesCache(episode_dir(data_dir, video_id) / NOTES_DIR)
