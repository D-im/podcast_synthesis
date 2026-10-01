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
    speaker_labels: bool = True
    poll_interval_seconds: float = 5.0
    max_wait_minutes: float = 180.0
    transcription_usd_per_hour: float = 0.23
    summarizer_input_usd_per_million: float = 2.0
    summarizer_output_usd_per_million: float = 10.0
    anthropic_max_output_tokens: int = 4096
    map_chunk_tokens: int = 60000
    map_output_tokens: int = 4096
    compress_before_upload: bool = True
    upload_bitrate_kbps: int = 32
    verifier_input_usd_per_million: float = 2.0
    verifier_output_usd_per_million: float = 10.0
    openai_max_input_tokens: int = 250000
    openai_max_output_tokens: int = 16000
    max_retries: int = 4
    retry_base_delay_seconds: float = 5.0
    retry_max_delay_seconds: float = 60.0


JS_RUNTIMES = ("node", "deno")
NO_VENDOR = ("fake", "none")  # not real vendors, so the same-vendor rule does not apply


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


def _optional_table(data: dict, key: str, path: Path) -> dict:
    t = data.get(key, {})
    if not isinstance(t, dict):
        raise ConfigError(f"{path}: [{key}] must be a table")
    return t


def _positive(table: dict, key: str, default: float, name: str, path: Path,
              allow_zero: bool = False) -> float:
    v = table.get(key, default)
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ConfigError(f"{path}: [{name}] '{key}' must be a number")
    v = float(v)
    if not math.isfinite(v) or v < 0 or (v == 0 and not allow_zero):
        raise ConfigError(
            f"{path}: [{name}] '{key}' must be a finite number, "
            f"{'not negative' if allow_zero else 'greater than 0'}")
    return v


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
    aai = _optional_table(data, "assemblyai", path)
    labels = aai.get("speaker_labels", True)
    if not isinstance(labels, bool):
        raise ConfigError(f"{path}: [assemblyai] 'speaker_labels' must be true or false")
    poll = _positive(aai, "poll_interval_seconds", 5.0, "assemblyai", path)
    max_wait = _positive(aai, "max_wait_minutes", 180.0, "assemblyai", path)
    compress = aai.get("compress_before_upload", True)
    if not isinstance(compress, bool):
        raise ConfigError(f"{path}: [assemblyai] 'compress_before_upload' must be true or false")
    bitrate = aai.get("upload_bitrate_kbps", 32)
    if isinstance(bitrate, bool) or not isinstance(bitrate, int) or not 16 <= bitrate <= 128:
        raise ConfigError(
            f"{path}: [assemblyai] 'upload_bitrate_kbps' must be an integer from 16 to 128")
    pricing = _optional_table(data, "pricing", path)
    per_hour = _positive(pricing, "transcription_usd_per_hour", 0.23, "pricing", path,
                         allow_zero=True)
    in_rate = _positive(pricing, "summarizer_input_usd_per_million", 2.0, "pricing", path,
                        allow_zero=True)
    out_rate = _positive(pricing, "summarizer_output_usd_per_million", 10.0, "pricing", path,
                         allow_zero=True)
    ver_in = _positive(pricing, "verifier_input_usd_per_million", 2.0, "pricing", path,
                       allow_zero=True)
    ver_out = _positive(pricing, "verifier_output_usd_per_million", 10.0, "pricing", path,
                        allow_zero=True)
    oai = _optional_table(data, "openai", path)
    oai_in = oai.get("max_input_tokens", 250000)
    if isinstance(oai_in, bool) or not isinstance(oai_in, int) or oai_in <= 0:
        raise ConfigError(f"{path}: [openai] 'max_input_tokens' must be a positive integer")
    oai_out = oai.get("max_output_tokens", 16000)
    if isinstance(oai_out, bool) or not isinstance(oai_out, int) or not 0 < oai_out <= 100000:
        raise ConfigError(
            f"{path}: [openai] 'max_output_tokens' must be an integer from 1 to 100000")
    rt = _optional_table(data, "retry", path)
    retries = rt.get("max_retries", 4)
    if isinstance(retries, bool) or not isinstance(retries, int) or not 0 <= retries <= 10:
        raise ConfigError(f"{path}: [retry] 'max_retries' must be an integer from 0 to 10")
    rt_base = _positive(rt, "base_delay_seconds", 5.0, "retry", path)
    rt_max = _positive(rt, "max_delay_seconds", 60.0, "retry", path)
    if rt_max < rt_base:
        raise ConfigError(
            f"{path}: [retry] 'max_delay_seconds' must not be below 'base_delay_seconds'")
    providers = _str_table(data, "providers", path)
    # AD-13: the checker must come from a different vendor than the writer
    v_norm, s_norm = providers["verifier"].strip().lower(), providers["summarizer"].strip().lower()
    if v_norm == s_norm and v_norm not in NO_VENDOR:
        raise ConfigError(
            f"{path}: [providers] the verifier and the summarizer are both "
            f"'{providers['verifier']}'; the checker must be a different vendor than the writer")
    anth = _optional_table(data, "anthropic", path)
    max_out = anth.get("max_output_tokens", 4096)
    if isinstance(max_out, bool) or not isinstance(max_out, int) or not 0 < max_out <= 16384:
        raise ConfigError(
            f"{path}: [anthropic] 'max_output_tokens' must be an integer from 1 to 16384")
    chunk = anth.get("map_chunk_tokens", 60000)
    if isinstance(chunk, bool) or not isinstance(chunk, int) or chunk <= 0:
        raise ConfigError(f"{path}: [anthropic] 'map_chunk_tokens' must be a positive integer")
    map_out = anth.get("map_output_tokens", 4096)
    if isinstance(map_out, bool) or not isinstance(map_out, int) or not 0 < map_out <= 16384:
        raise ConfigError(
            f"{path}: [anthropic] 'map_output_tokens' must be an integer from 1 to 16384")
    return Config(
        map_chunk_tokens=chunk,
        map_output_tokens=map_out,
        summarizer_input_usd_per_million=in_rate,
        summarizer_output_usd_per_million=out_rate,
        anthropic_max_output_tokens=max_out,
        js_runtime=runtime,
        speaker_labels=labels,
        compress_before_upload=compress,
        upload_bitrate_kbps=bitrate,
        poll_interval_seconds=poll,
        max_wait_minutes=max_wait,
        transcription_usd_per_hour=per_hour,
        port=port,
        daily_cap_usd=cap,
        token_limit=limit,
        verifier_input_usd_per_million=ver_in,
        verifier_output_usd_per_million=ver_out,
        openai_max_input_tokens=oai_in,
        openai_max_output_tokens=oai_out,
        max_retries=retries,
        retry_base_delay_seconds=rt_base,
        retry_max_delay_seconds=rt_max,
        providers=providers,
        models=_str_table(data, "models", path, MODEL_ROLES),
        accuracy_threshold=acc,
        coverage_threshold=cov,
    )
