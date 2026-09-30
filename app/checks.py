"""Host prerequisite detection: ffmpeg and a JavaScript runtime."""
from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass


@dataclass(frozen=True)
class MissingTool:
    name: str
    looked_in: str
    detail: str = ""

    def describe(self) -> str:
        msg = f"{self.name} not found (looked in {self.looked_in})"
        return f"{msg}: {self.detail}" if self.detail else msg


def _version(exe: str) -> tuple[int, ...] | None:
    try:
        out = subprocess.run(
            [exe, "--version"], capture_output=True, text=True, timeout=10
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", out)
    if not m:
        return None
    return tuple(int(g) for g in m.groups() if g is not None)


def check_tools(path: str | None = None) -> list[MissingTool]:
    """Return missing tools. `path` overrides PATH (defaults to the real one)."""
    import os

    where = f"PATH={path if path is not None else os.environ.get('PATH', '')}"
    missing: list[MissingTool] = []
    if not shutil.which("ffmpeg", path=path):
        missing.append(MissingTool("ffmpeg", where))

    ok = False
    for name, minimum in (("node", (22,)), ("deno", (2, 3))):
        exe = shutil.which(name, path=path)
        if exe:
            v = _version(exe)
            if v and v[: len(minimum)] >= minimum:
                ok = True
                break
    if not ok:
        missing.append(
            MissingTool(
                "JavaScript runtime",
                where,
                "need node >=22 or deno >=2.3",
            )
        )
    return missing
