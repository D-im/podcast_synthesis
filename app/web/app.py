from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from urllib.parse import parse_qs

from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.core import submit as core_submit
from app.core.meter import format_usd, local_day_bounds_utc, to_micro
from app.ports import OnePager, Transcript
from app.store import artifacts, db, episodes, spend
from app.web.status import describe_job, describe_spend, format_timestamp

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def create_app(warnings: list[str], data_dir: Path = db.DEFAULT_DATA_DIR,
               daily_cap_usd: float = 5.0) -> FastAPI:
    app = FastAPI()
    app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")),
              name="static")

    cap_text = format_usd(to_micro(daily_cap_usd), 2)

    def today_spend() -> str:
        """Header text: today's (local day) spend against the Daily Cap. Read-only."""
        try:
            start, end = local_day_bounds_utc()
            with closing(db.connect(data_dir)) as conn:
                total = spend.total_between(conn, start, end)
            return f"Today: {format_usd(total, 2)} of {cap_text}"
        except (sqlite3.Error, OSError, RuntimeError):
            return f"Today: unavailable (cap {cap_text})"

    def home_response(request: Request, status_code: int = 200, error: str | None = None,
                      url: str = ""):
        return TEMPLATES.TemplateResponse(
            request,
            "home.html",
            {"warnings": warnings, "error": error, "url": url,
             "today_spend": today_spend()},
            status_code=status_code,
        )

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request):
        return home_response(request)

    @app.post("/submit")
    async def submit(request: Request):
        body = (await request.body()).decode("utf-8", errors="replace")
        url = (parse_qs(body).get("url") or [""])[0]

        def work():
            # own connection inside the worker thread: sqlite connections are thread-bound
            with closing(db.connect(data_dir)) as conn:
                return core_submit.submit(conn, url)

        try:
            result = await run_in_threadpool(work)
        except (sqlite3.Error, OSError, RuntimeError):
            return home_response(
                request, 503,
                "Could not save that link just now (the database is busy or unavailable). "
                "Nothing was created. Please try again.", url)
        if isinstance(result, core_submit.Rejected):
            return home_response(request, 400, result.reason, url)
        return RedirectResponse(f"/episodes/{result.video_id}", status_code=303)

    def load(video_id: str):
        with closing(db.connect(data_dir)) as conn:
            episode = episodes.get_episode(conn, video_id)
            job = episodes.get_latest_job_with_steps(conn, video_id) if episode else None
            latest = episodes.latest_one_pager_version(conn, video_id) if episode else None
            if episode is not None:
                cost = describe_spend(spend.total_for_episode(conn, video_id),
                                      spend.by_step(conn, video_id))
        if episode is None:
            raise HTTPException(status_code=404, detail="Episode not found")
        status = describe_job(episode, job, cost)
        status["one_pager"] = None
        status["one_pager_error"] = None
        summarized = any(s["name"] == "summarize" and s["state"] == "done"
                         for s in (job or {}).get("steps", []))
        if summarized and latest is not None:
            try:
                page = OnePager.from_dict(artifacts.read_json(
                    artifacts.one_pager_path(data_dir, video_id, latest["version"])))
                status["one_pager"] = {
                    "version": latest["version"],
                    "sections": [{"name": x.name, "text": x.text} for x in page.sections],
                }
            except (ValueError, OSError, KeyError, TypeError):
                # unreadable artifact: say so instead of silently showing nothing
                status["one_pager_error"] = f"One-Pager v{latest['version']} could not be read."
        elif summarized:
            status["one_pager_error"] = "No One-Pager version is recorded for this episode."

        return episode, status

    @app.get("/episodes/{video_id}", response_class=HTMLResponse)
    def episode_page(request: Request, video_id: str):
        episode, status = load(video_id)
        return TEMPLATES.TemplateResponse(
            request, "episode.html",
            {"episode": episode, "status": status, "today_spend": today_spend()},
        )

    @app.get("/episodes/{video_id}/status", response_class=HTMLResponse)
    def episode_status(request: Request, video_id: str):
        episode, status = load(video_id)
        response = TEMPLATES.TemplateResponse(
            request, "_status.html", {"episode": episode, "status": status}
        )
        response.headers["Cache-Control"] = "no-store"  # never reuse a stale poll
        return response

    @app.get("/episodes/{video_id}/transcript", response_class=HTMLResponse)
    def transcript_page(request: Request, video_id: str):
        try:
            path = artifacts.artifact_path(data_dir, video_id, artifacts.TRANSCRIPT)
            transcript = Transcript.from_dict(artifacts.read_json(path))
            lines = [{"time": format_timestamp(s.start), "speaker": s.speaker, "text": s.text}
                     for s in transcript.segments]
        except (ValueError, OSError, KeyError, TypeError, OverflowError):
            raise HTTPException(status_code=404, detail="Transcript not found") from None
        with closing(db.connect(data_dir)) as conn:
            episode = episodes.get_episode(conn, video_id)
        return TEMPLATES.TemplateResponse(
            request, "transcript.html",
            {"video_id": video_id, "title": (episode or {}).get("title") or video_id,
             "lines": lines, "today_spend": today_spend()},
        )

    return app
