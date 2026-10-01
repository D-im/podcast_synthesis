"""Composition root: wires adapters to ports by config name and runs Jobs serially."""
from __future__ import annotations

import logging
import os
import threading
from contextlib import closing
from dataclasses import replace
from pathlib import Path

from app import env
from app.adapters import fakes
from app.config import Config
from app.core import pipeline, retry
from app.ports import Adapters
from app.store import db, episodes

log = logging.getLogger(__name__)


def build_adapters(config: Config, behavior: fakes.FakeBehavior | None = None) -> Adapters:
    """Map provider names from config.toml to adapters."""
    p = config.providers
    if p["downloader"] not in ("fake", "yt-dlp"):
        raise ValueError(f"provider '{p['downloader']}' for downloader is not available yet")
    if p["transcriber"] not in ("fake", "assemblyai"):
        raise ValueError(f"provider '{p['transcriber']}' for transcriber is not available yet")
    if p["summarizer"] not in ("fake", "anthropic"):
        raise ValueError(f"provider '{p['summarizer']}' for summarizer is not available yet")
    if p["verifier"] not in ("fake", "none", "openai"):
        raise ValueError(f"provider '{p['verifier']}' for verifier is not available yet")
    adapters = fakes.build_fakes(behavior, verifier=p["verifier"] == "fake")
    if p["downloader"] == "yt-dlp":
        from app.adapters.ytdlp import YtDlpDownloader

        adapters = replace(adapters, downloader=YtDlpDownloader(config.js_runtime))
    if p["transcriber"] == "assemblyai":
        from app.adapters.assemblyai import AssemblyAITranscriber

        adapters = replace(adapters, transcriber=AssemblyAITranscriber.from_config(config))
    if p["summarizer"] == "anthropic":
        from app.adapters.anthropic import AnthropicSummarizer

        adapters = replace(adapters, summarizer=AnthropicSummarizer.from_config(config))
    if p["verifier"] == "openai":
        from app.adapters.openai import OpenAIVerifier

        adapters = replace(adapters, verifier=OpenAIVerifier.from_config(config))
    return adapters


def configured_secrets() -> list[str]:
    return [v for k in env.REQUIRED_KEYS if (v := os.environ.get(k))]


class Worker:
    def __init__(self, data_dir: Path, adapters: Adapters, poll_interval: float = 0.2,
                 secrets=configured_secrets):
        self.data_dir = Path(data_dir)
        self.adapters = adapters
        self.poll_interval = poll_interval
        self.secrets = secrets
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def run_next(self) -> bool:
        """Run the oldest queued Job, if any. Returns whether one ran."""
        with closing(db.connect(self.data_dir)) as conn:
            job = episodes.next_queued_job(conn)
            if job is None:
                return False
            pipeline.run_job(conn, self.data_dir, job, self.adapters, self.secrets)
            return True

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                ran = self.run_next()
            except retry.Stopping:
                return  # shut down during a backoff wait; the Job stays running and is recovered
            except Exception as e:  # never log the message: it could hold transcript text
                log.error("worker error: %s", type(e).__name__)
                ran = False
            if not ran:
                self._stop.wait(self.poll_interval)

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        retry.clear_stop()
        self._thread = threading.Thread(target=self._loop, name="worker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        retry.request_stop()   # wakes a backoff wait
        if self._thread is not None:
            self._thread.join(timeout=5)  # a long step must not hang shutdown
            alive = self._thread.is_alive()
            self._thread = None
            if not alive:
                retry.clear_stop()
