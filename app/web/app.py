from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from urllib.parse import parse_qs

from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.core import submit as core_submit
from app.store import db, episodes

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def create_app(warnings: list[str], data_dir: Path = db.DEFAULT_DATA_DIR) -> FastAPI:
    app = FastAPI()

    def home_response(request: Request, status_code: int = 200, error: str | None = None,
                      url: str = ""):
        return TEMPLATES.TemplateResponse(
            request,
            "home.html",
            {"warnings": warnings, "error": error, "url": url},
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

    @app.get("/episodes/{video_id}", response_class=HTMLResponse)
    def episode_page(request: Request, video_id: str):
        with closing(db.connect(data_dir)) as conn:
            episode = episodes.get_episode(conn, video_id)
            job = episodes.get_latest_job_with_steps(conn, video_id) if episode else None
        if episode is None:
            raise HTTPException(status_code=404, detail="Episode not found")
        return TEMPLATES.TemplateResponse(
            request, "episode.html", {"episode": episode, "job": job}
        )

    return app
