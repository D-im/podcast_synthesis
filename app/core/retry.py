"""Retry with exponential backoff for brief network or vendor trouble (Story 3.1)."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Callable, TypeVar

T = TypeVar("T")

_STOP = threading.Event()


class Stopping(BaseException):
    """Raised inside a backoff wait when the app is shutting down.

    Not an Exception on purpose: the pipeline must not record a failure, so the Job stays
    `running` and is recovered at the next start.
    """


def request_stop() -> None:
    _STOP.set()


def clear_stop() -> None:
    _STOP.clear()


def stop_aware_sleep(seconds: float) -> None:
    """Wait, but give up at once when shutdown is requested."""
    if _STOP.wait(seconds):
        raise Stopping()


def call_with_backoff(fn: Callable[[], T], is_transient: Callable[[BaseException], bool],
                      retries: int, base_delay: float, max_delay: float,
                      sleep: Callable[[float], None] = time.sleep) -> T:
    """Call `fn`; on a transient error wait min(base * 2**n, max) and call again, `retries` times.

    Any other exception, and the last transient one, is raised unchanged.
    """
    attempt = 0
    while True:
        try:
            return fn()
        except Exception as e:
            if attempt >= retries or not is_transient(e):
                raise
            sleep(min(base_delay * 2 ** attempt, max_delay))
            attempt += 1


@dataclass(frozen=True)
class Retry:
    """Retry settings carried by an adapter. The default never retries."""
    retries: int = 0
    base_delay: float = 5.0
    max_delay: float = 60.0
    sleep: Callable[[float], None] = field(default=stop_aware_sleep, compare=False)

    @classmethod
    def from_config(cls, config) -> "Retry":
        return cls(config.max_retries, config.retry_base_delay_seconds,
                   config.retry_max_delay_seconds)

    def call(self, fn: Callable[[], T], is_transient: Callable[[BaseException], bool]) -> T:
        return call_with_backoff(fn, is_transient, self.retries, self.base_delay,
                                 self.max_delay, self.sleep)
