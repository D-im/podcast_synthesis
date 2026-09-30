"""Secrets handling: environment first, then a git-ignored .env. Names only."""
from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from dotenv import dotenv_values

from app.config import PROJECT_ROOT

REQUIRED_KEYS = ("ASSEMBLYAI_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY")
DEFAULT_ENV_PATH = PROJECT_ROOT / ".env"


def load_env(
    env_path: Path = DEFAULT_ENV_PATH, environ: dict[str, str] | None = None
) -> None:
    """Add .env values to the environment without overriding real env vars."""
    target = os.environ if environ is None else environ
    if not Path(env_path).is_file():
        return
    for k, v in dotenv_values(env_path).items():
        if v and not target.get(k):
            target[k] = v


def missing_keys(environ: Mapping[str, str] | None = None) -> list[str]:
    env = os.environ if environ is None else environ
    return [k for k in REQUIRED_KEYS if not env.get(k)]
