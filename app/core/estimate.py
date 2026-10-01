"""Pure cost estimate for regenerating a One-Pager (summarize and verify steps).

No I/O. Tokens are `ceil(characters / 3)`. Amounts are integer micro-dollars. Every figure is an
upper-bound style estimate; real cost is recorded by the Meter. Story 3.3 extends this with
transcription cost (`estimate_job`).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

PROMPT_TOKENS = 1500          # summarize prompts, per call
VERIFY_PROMPT_TOKENS = 1000   # ideas and claims pass prompts
COVERAGE_PROMPT_TOKENS = 2000
IDEAS_OUTPUT_TOKENS = 2000    # assumed
CLAIMS_OUTPUT_TOKENS = 8000   # assumed: the model spends many hidden reasoning tokens
COVERAGE_OUTPUT_TOKENS = 1500
NO_COST_PROVIDERS = ("none", "fake")
TRANSCRIPT_CHARS_PER_SECOND = 18   # spoken text with timestamps and speaker labels, rounded up


def tokens_for(characters: int) -> int:
    return math.ceil(characters / 3)


@dataclass(frozen=True)
class Prices:
    """Prices and limits, taken from the current config."""
    summarizer_input_usd_per_million: float
    summarizer_output_usd_per_million: float
    verifier_input_usd_per_million: float
    verifier_output_usd_per_million: float
    token_limit: int
    map_chunk_tokens: int
    map_output_tokens: int
    anthropic_max_output_tokens: int
    openai_max_output_tokens: int
    verifier_provider: str
    transcription_usd_per_hour: float = 0.0
    transcriber_provider: str = ""

    @classmethod
    def from_config(cls, config) -> "Prices":
        return cls(
            config.summarizer_input_usd_per_million, config.summarizer_output_usd_per_million,
            config.verifier_input_usd_per_million, config.verifier_output_usd_per_million,
            config.token_limit, config.map_chunk_tokens, config.map_output_tokens,
            config.anthropic_max_output_tokens, config.openai_max_output_tokens,
            config.providers["verifier"],
            config.transcription_usd_per_hour, config.providers["transcriber"])


@dataclass(frozen=True)
class Estimate:
    summarize_micro: int
    verify_micro: int
    total_micro: int
    assumptions: dict = field(default_factory=dict)
    transcribe_micro: int = 0


def _cost(input_tokens: int, input_price: float, output_tokens: int, output_price: float) -> Decimal:
    """Micro-dollars (unrounded): tokens times dollars per million tokens."""
    return (Decimal(input_tokens) * Decimal(str(input_price))
            + Decimal(output_tokens) * Decimal(str(output_price)))


def _round(value: Decimal) -> int:
    return int(value.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def estimate_regeneration(transcript_tokens: int, one_pager_tokens: int,
                          prices: Prices) -> Estimate:
    """Estimate summarize and verify for a Transcript of `transcript_tokens` and a latest
    One-Pager of `one_pager_tokens`."""
    t = max(0, int(transcript_tokens))
    page = max(0, int(one_pager_tokens))
    p = prices
    in_s, out_s = p.summarizer_input_usd_per_million, p.summarizer_output_usd_per_million
    assumptions: dict = {"transcript_tokens": t, "one_pager_tokens": page}

    if t <= p.token_limit:
        summarize = _cost(t + PROMPT_TOKENS, in_s, p.anthropic_max_output_tokens, out_s)
        assumptions["summarize"] = {
            "mode": "single call", "input_tokens": t + PROMPT_TOKENS,
            "output_tokens": p.anthropic_max_output_tokens}
    else:
        size = max(1, min(p.map_chunk_tokens, p.token_limit))
        chunks = math.ceil(t / size)
        map_in = t + PROMPT_TOKENS * chunks
        map_out = chunks * p.map_output_tokens
        reduce_in = chunks * p.map_output_tokens + PROMPT_TOKENS
        summarize = (_cost(map_in, in_s, map_out, out_s)
                     + _cost(reduce_in, in_s, p.anthropic_max_output_tokens, out_s))
        assumptions["summarize"] = {
            "mode": "map-reduce", "chunks": chunks, "map_input_tokens": map_in,
            "map_output_tokens": map_out, "reduce_input_tokens": reduce_in,
            "reduce_output_tokens": p.anthropic_max_output_tokens}

    if p.verifier_provider.strip().lower() in NO_COST_PROVIDERS:
        verify = Decimal(0)
        assumptions["verify"] = {"provider": p.verifier_provider, "cost": "none"}
    else:
        cap = p.openai_max_output_tokens
        ideas_out = min(IDEAS_OUTPUT_TOKENS, cap)
        claims_out = min(CLAIMS_OUTPUT_TOKENS, cap)
        coverage_out = min(COVERAGE_OUTPUT_TOKENS, cap)
        ideas_in = t + VERIFY_PROMPT_TOKENS
        claims_in = t + page + VERIFY_PROMPT_TOKENS
        coverage_in = COVERAGE_PROMPT_TOKENS + page
        vi, vo = p.verifier_input_usd_per_million, p.verifier_output_usd_per_million
        verify = (_cost(ideas_in, vi, ideas_out, vo) + _cost(claims_in, vi, claims_out, vo)
                  + _cost(coverage_in, vi, coverage_out, vo))
        assumptions["verify"] = {
            "provider": p.verifier_provider,
            "ideas": {"input_tokens": ideas_in, "output_tokens": ideas_out},
            "claims": {"input_tokens": claims_in, "output_tokens": claims_out},
            "coverage": {"input_tokens": coverage_in, "output_tokens": coverage_out}}

    s_micro, v_micro = _round(summarize), _round(verify)
    return Estimate(s_micro, v_micro, s_micro + v_micro, assumptions)


def estimate_job(duration_seconds: float, prices: Prices) -> Estimate:
    """Estimate a whole Job (transcribe, summarize, verify) from the video's duration.

    The Transcript size is assumed from the duration; a fake transcriber costs nothing.
    """
    seconds = max(0.0, float(duration_seconds))
    tokens = tokens_for(math.ceil(seconds * TRANSCRIPT_CHARS_PER_SECOND))
    rest = estimate_regeneration(tokens, prices.anthropic_max_output_tokens, prices)
    if prices.transcriber_provider.strip().lower() in NO_COST_PROVIDERS:
        transcribe = 0
    else:
        transcribe = _round(Decimal(str(seconds)) / Decimal(3600)
                            * Decimal(str(prices.transcription_usd_per_hour))
                            * Decimal(1_000_000))
    assumptions = {**rest.assumptions, "duration_seconds": seconds,
                   "transcribe": {"usd_per_hour": prices.transcription_usd_per_hour,
                                  "provider": prices.transcriber_provider}}
    return Estimate(rest.summarize_micro, rest.verify_micro,
                    transcribe + rest.total_micro, assumptions, transcribe)
