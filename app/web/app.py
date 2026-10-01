from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from urllib.parse import parse_qs

from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.core import submit as core_submit
from app.core.meter import format_usd, local_day_bounds_utc, to_micro
from app.ports import OnePager, Transcript
from app.store import artifacts, db, episodes, spend
from app.web.library import build_library, local_datetime, percent
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
                      url: str = "", q: str | None = None):
        try:
            with closing(db.connect(data_dir)) as conn:
                library = build_library(conn, data_dir, q)
            library["unavailable"] = False
        except (sqlite3.Error, OSError, RuntimeError, KeyError, TypeError, ValueError):
            library = {"query": "", "rows": [], "truncated": False, "unavailable": True}
        return TEMPLATES.TemplateResponse(
            request,
            "home.html",
            {"warnings": warnings, "error": error, "url": url,
             "library": library, "today_spend": today_spend()},
            status_code=status_code,
        )

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request):
        return home_response(request, q=request.query_params.get("q"))

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
        status["date"] = local_datetime(episode["created_at"])
        status["audio_ready"] = False
        try:
            status["audio_ready"] = artifacts.exists(
                artifacts.artifact_path(data_dir, video_id, artifacts.AUDIO))
        except ValueError:
            pass
        status["verdict"] = None   # filled by Epic 2
        status["fidelity"] = None
        status["verification"] = None
        status["verification_skipped"] = None
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

        if latest is not None:  # advisory only: nothing here may touch the One-Pager above
            status["verification"] = read_verification(video_id, latest["version"])
            if status["verification"] is not None:
                status["fidelity"] = status["verification"]["accuracy"]
        verify_step = next((s for s in (job or {}).get("steps", [])
                            if s["name"] == "verify"), None)
        if (status["verification"] is None and verify_step is not None
                and verify_step["state"] == "skipped" and verify_step.get("message")):
            status["verification_skipped"] = verify_step["message"]

        return episode, status

    def read_verification(video_id: str, version: int) -> dict | None:
        try:
            data = artifacts.read_json(artifacts.verification_path(data_dir, video_id, version))
            pct = percent(data["accuracy"])
            if pct is None:
                return None
            unsupported = [
                {"section": str(u["section"]), "claim": str(u["claim"]), "note": str(u["note"])}
                for u in data["unsupported_claims"]]
            return {"version": version, "accuracy": pct, "checked": len(data["claims"]),
                    "unsupported": unsupported}
        except (ValueError, OSError, KeyError, TypeError, RecursionError):
            return None

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

    @app.get("/episodes/{video_id}/audio")
    def audio(video_id: str):
        try:
            path = artifacts.artifact_path(data_dir, video_id, artifacts.AUDIO)
        except ValueError:
            raise HTTPException(status_code=404, detail="Audio not found") from None
        if not artifacts.exists(path):
            raise HTTPException(status_code=404, detail="Audio not found")
        try:
            # inline so the page's <audio> plays it; the link's `download` attribute saves it
            return FileResponse(path, media_type="audio/mp4", filename=f"{video_id}.m4a",
                                content_disposition_type="inline")
        except OSError:
            raise HTTPException(status_code=404, detail="Audio not found") from None

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
