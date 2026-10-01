from __future__ import annotations

import re
import sqlite3
from contextlib import closing
from pathlib import Path
from urllib.parse import parse_qs

from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import ConfigError, DEFAULT_CONFIG_PATH, load_config
from app.core import regenerate as core_regen
from app.core import submit as core_submit
from app.core.meter import format_usd, local_day_bounds_utc, to_micro
from app.ports import OnePager, Transcript
from app.store import artifacts, db, episodes, spend
from app.web.flags import Thresholds, flags, read_thresholds
from app.web.library import build_library, local_datetime, percent
from app.web.status import describe_job, describe_spend, format_timestamp

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def create_app(warnings: list[str], data_dir: Path = db.DEFAULT_DATA_DIR,
               daily_cap_usd: float = 5.0, config_path: Path | None = None,
               thresholds: Thresholds = Thresholds()) -> FastAPI:
    app = FastAPI()
    app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")),
              name="static")

    cap_text = format_usd(to_micro(daily_cap_usd), 2)
    current = {"t": thresholds}

    def current_thresholds() -> Thresholds:
        """Re-read on each request; an unreadable or invalid file keeps the last good values."""
        current["t"] = read_thresholds(config_path, current["t"])
        return current["t"]

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
                library = build_library(conn, data_dir, q, current_thresholds())
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
            versions = episodes.list_one_pager_versions(conn, video_id) if episode else []
            can_regen = (core_regen.can_regenerate(conn, data_dir, video_id)
                         if episode else False)
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
        status["flags"] = []
        status["one_pager"] = None
        status["one_pager_error"] = None
        summarized = any(s["name"] == "summarize" and s["state"] == "done"
                         for s in (job or {}).get("steps", []))
        status["can_regenerate"] = can_regen
        status["history"] = core_regen.history(versions)
        for row in status["history"]:
            row["date"] = local_datetime(row["created_at"])
        # the latest successful version stays shown even while a regeneration is queued,
        # running or failed
        if latest is not None:
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
            v = status["verification"]
            status["flags"] = flags(v and v["accuracy_raw"], v and v["coverage_raw"],
                                    current_thresholds()) if v else []
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
            cov_raw = data.get("coverage")
            cov_pct = percent(cov_raw)
            if cov_pct is None:
                cov_raw = None
            missed = []
            if cov_raw is not None:
                for m in data.get("missed_ideas") or []:
                    if isinstance(m, dict) and m.get("title"):   # a bad entry is skipped
                        missed.append({"title": str(m["title"]),
                                       "coverage": str(m.get("coverage", "")),
                                       "note": str(m.get("note", ""))})
            return {"version": version, "accuracy": pct, "accuracy_raw": data["accuracy"],
                    "coverage": cov_pct, "coverage_raw": cov_raw, "missed": missed,
                    "checked": len(data["claims"]), "unsupported": unsupported}
        except (ValueError, OSError, KeyError, TypeError, RecursionError):
            return None

    @app.get("/episodes/{video_id}", response_class=HTMLResponse)
    def episode_page(request: Request, video_id: str):
        episode, status = load(video_id)
        return TEMPLATES.TemplateResponse(
            request, "episode.html",
            {"episode": episode, "status": status, "today_spend": today_spend()},
        )

    def regenerate_page(request: Request, video_id: str, status_code: int = 200,
                        notice: str | None = None):
        """The confirmation page. Read-only: it never queues anything."""
        ctx = {"video_id": video_id, "title": video_id, "message": None, "notice": notice,
               "estimate": None, "today_spend": today_spend()}
        try:
            with closing(db.connect(data_dir)) as conn:
                episode = episodes.get_episode(conn, video_id)
                if episode is None:
                    raise HTTPException(status_code=404, detail="Episode not found")
                ctx["title"] = episode.get("title") or video_id
                try:
                    core_regen.check_preconditions(conn, data_dir, video_id)
                except core_regen.Blocked as e:
                    ctx["message"] = e.message
                    return TEMPLATES.TemplateResponse(request, "regenerate.html", ctx,
                                                      status_code=409)
                try:
                    config = load_config(config_path or DEFAULT_CONFIG_PATH)
                    core_regen.Prices.from_config(config)   # fails early on unusable prices
                except (ConfigError, KeyError, TypeError, ValueError, OSError):
                    ctx["message"] = ("The config file could not be read, so no estimate can "
                                      "be made. Nothing was started.")
                    return TEMPLATES.TemplateResponse(request, "regenerate.html", ctx,
                                                      status_code=status_code)
                try:
                    est = core_regen.estimate_for_episode(conn, data_dir, video_id, config)
                except core_regen.Blocked as e:
                    ctx["message"] = e.message
                    return TEMPLATES.TemplateResponse(request, "regenerate.html", ctx,
                                                      status_code=409)
                try:
                    start, end = local_day_bounds_utc()
                    today = spend.total_between(conn, start, end)
                except (sqlite3.Error, OSError, RuntimeError):
                    today = None
        except (sqlite3.Error, OSError):
            ctx["message"] = "The database is unavailable just now. Nothing was started."
            return TEMPLATES.TemplateResponse(request, "regenerate.html", ctx,
                                              status_code=503)
        cap = to_micro(config.daily_cap_usd)
        ctx["estimate"] = {
            "summarize": format_usd(est.summarize_micro), "verify": format_usd(est.verify_micro),
            "total": format_usd(est.total_micro), "micro": est.total_micro,
            "verify_free": est.verify_micro == 0,
            "verifier": config.providers["verifier"],
            "today": None if today is None else format_usd(today, 2),
            "cap": format_usd(cap, 2),
            "after": None if today is None else format_usd(today + est.total_micro, 2),
            "over_cap": today is not None and today + est.total_micro > cap,
            "map_reduce": est.assumptions["summarize"]["mode"] == "map-reduce",
        }
        response = TEMPLATES.TemplateResponse(request, "regenerate.html", ctx,
                                              status_code=status_code)
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/episodes/{video_id}/regenerate", response_class=HTMLResponse)
    def regenerate_get(request: Request, video_id: str):
        return regenerate_page(request, video_id)

    @app.post("/episodes/{video_id}/regenerate")
    async def regenerate_post(request: Request, video_id: str):
        body = (await request.body()).decode("utf-8", errors="replace")
        posted = (parse_qs(body).get("estimate") or [""])[0].strip()

        def work():
            with closing(db.connect(data_dir)) as conn:
                core_regen.check_preconditions(conn, data_dir, video_id)
                try:
                    config = load_config(config_path or DEFAULT_CONFIG_PATH)
                    core_regen.Prices.from_config(config)
                except (ConfigError, KeyError, TypeError, ValueError, OSError):
                    return "config"
                est = core_regen.estimate_for_episode(conn, data_dir, video_id, config)
                if not re.fullmatch(r"[0-9]{1,18}", posted) or int(posted) != est.total_micro:
                    return "stale"
                core_regen.enqueue(conn, data_dir, video_id)
                return "queued"

        try:
            outcome = await run_in_threadpool(work)
        except core_regen.UnknownEpisode:
            raise HTTPException(status_code=404, detail="Episode not found") from None
        except core_regen.Blocked as e:
            return TEMPLATES.TemplateResponse(
                request, "regenerate.html",
                {"video_id": video_id, "title": video_id, "message": e.message, "notice": None,
                 "estimate": None, "today_spend": today_spend()}, status_code=409)
        except (sqlite3.Error, OSError):
            return TEMPLATES.TemplateResponse(
                request, "regenerate.html",
                {"video_id": video_id, "title": video_id, "notice": None, "estimate": None,
                 "message": "The database is unavailable just now. Nothing was started.",
                 "today_spend": today_spend()}, status_code=503)
        if outcome == "queued":
            return RedirectResponse(f"/episodes/{video_id}", status_code=303)
        if outcome == "stale":
            return await run_in_threadpool(
                regenerate_page, request, video_id, 200,
                "The estimate changed since you last looked (the transcript, the One-Pager or "
                "the prices changed). Nothing was started. Please check the new figures.")
        return await run_in_threadpool(regenerate_page, request, video_id)

    @app.get("/episodes/{video_id}/versions/{number}", response_class=HTMLResponse)
    def version_page(request: Request, video_id: str, number: str):
        if not re.fullmatch(r"[1-9][0-9]{0,8}", number):
            raise HTTPException(status_code=404, detail="Version not found")
        n = int(number)
        try:
            with closing(db.connect(data_dir)) as conn:
                if episodes.get_episode(conn, video_id) is None:
                    raise HTTPException(status_code=404, detail="Episode not found")
                versions = episodes.list_one_pager_versions(conn, video_id)
        except (sqlite3.Error, OSError):
            raise HTTPException(status_code=503, detail="The database is unavailable just now") \
                from None
        row = next((v for v in versions if v["version"] == n), None)
        if row is None:
            raise HTTPException(status_code=404, detail="Version not found")
        sections, error = [], None
        try:
            page = OnePager.from_dict(artifacts.read_json(
                artifacts.one_pager_path(data_dir, video_id, n)))
            sections = [{"name": x.name, "text": x.text} for x in page.sections]
        except (ValueError, OSError, KeyError, TypeError, RecursionError):
            error = f"One-Pager v{n} could not be read."
        verification = read_verification(video_id, n)
        return TEMPLATES.TemplateResponse(
            request, "version.html",
            {"video_id": video_id, "version": n, "row": row,
             "date": local_datetime(row["created_at"]), "is_latest": n == versions[-1]["version"],
             "sections": sections, "error": error, "verification": verification,
             "today_spend": today_spend()},
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
