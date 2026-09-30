"""Load and validate config.toml into a typed object."""
from __future__ import annotations

import math
import tomllib
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.toml"


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Config:
    port: int
    daily_cap_usd: float
    token_limit: int
    providers: dict[str, str]
    models: dict[str, str]
    accuracy_threshold: float
    coverage_threshold: float
    js_runtime: str = "node"


JS_RUNTIMES = ("node", "deno")


def _num(data: dict, key: str, path: Path, kinds=(int, float)):
    if key not in data:
        raise ConfigError(f"{path}: missing required key '{key}'")
    v = data[key]
    if isinstance(v, bool) or not isinstance(v, kinds):
        raise ConfigError(f"{path}: '{key}' has invalid type {type(v).__name__}")
    return v


ROLES = ("downloader", "transcriber", "summarizer", "verifier")
MODEL_ROLES = ("transcriber", "summarizer", "verifier")  # the downloader has no model


def _str_table(data: dict, key: str, path: Path, roles=ROLES) -> dict[str, str]:
    t = data.get(key)
    if not isinstance(t, dict) or not t:
        raise ConfigError(f"{path}: missing or invalid table [{key}]")
    for role in roles:
        if role not in t:
            raise ConfigError(f"{path}: [{key}] is missing '{role}'")
    for k, v in t.items():
        if not isinstance(v, str) or not v:
            raise ConfigError(f"{path}: [{key}] '{k}' must be a non-empty string")
    return t


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> Config:
    path = Path(path)
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        raise ConfigError(f"{path}: config file not found") from None
    except OSError as e:
        raise ConfigError(f"{path}: cannot read config file ({e.strerror})") from None
    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as e:
        raise ConfigError(f"{path}: invalid TOML ({e})") from None

    port = _num(data, "port", path, (int,))
    if not 1 <= port <= 65535:
        raise ConfigError(f"{path}: 'port' must be between 1 and 65535")
    cap = float(_num(data, "daily_cap_usd", path))
    if not math.isfinite(cap) or cap < 0:
        raise ConfigError(f"{path}: 'daily_cap_usd' must be a finite number, not negative")
    limit = _num(data, "token_limit", path, (int,))
    if limit <= 0:
        raise ConfigError(f"{path}: 'token_limit' must be positive")
    fid = data.get("fidelity")
    if not isinstance(fid, dict):
        raise ConfigError(f"{path}: missing or invalid table [fidelity]")
    acc = float(_num(fid, "accuracy_threshold", path))
    cov = float(_num(fid, "coverage_threshold", path))
    for name, v in (("accuracy_threshold", acc), ("coverage_threshold", cov)):
        if not 0 <= v <= 1:
            raise ConfigError(f"{path}: [fidelity] '{name}' must be between 0 and 1")
    yt = data.get("ytdlp")
    if not isinstance(yt, dict):
        raise ConfigError(f"{path}: missing or invalid table [ytdlp]")
    runtime = yt.get("js_runtime")
    if runtime not in JS_RUNTIMES:
        raise ConfigError(f"{path}: [ytdlp] 'js_runtime' must be one of {', '.join(JS_RUNTIMES)}")
    return Config(
        js_runtime=runtime,
        port=port,
        daily_cap_usd=cap,
        token_limit=limit,
        providers=_str_table(data, "providers", path),
        models=_str_table(data, "models", path, MODEL_ROLES),
        accuracy_threshold=acc,
        coverage_threshold=cov,
    )
