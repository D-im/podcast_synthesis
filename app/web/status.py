"""Pure mapping from an Episode and its latest Job to a status view model."""
from __future__ import annotations

from app.core.meter import format_usd
from app.core.submit import STEP_NAMES
from app.core.text import format_timestamp  # noqa: F401  (re-exported for the web layer)

RUNNING_LABELS = {
    "download": "Downloading",
    "transcribe": "Transcribing",
    "summarize": "Summarizing",
    "verify": "Verifying",
}
POLLING_STATES = ("queued", "running")


def describe_spend(total_micro: int = 0, by_step: dict[str, int] | None = None) -> dict:
    return {
        "total": format_usd(total_micro),
        "by_step": {k: format_usd(v) for k, v in (by_step or {}).items()},
    }


def describe_job(episode: dict, job: dict | None, spend: dict | None = None) -> dict:
    spend = spend or describe_spend()
    title = episode.get("title") or episode["video_id"]
    if job is None:
        return {"title": title, "label": "No job", "polling": False, "failed_step": None,
                "message": None, "steps": [], "state": None, "spend": spend}
    steps = sorted(job["steps"], key=lambda s: STEP_NAMES.index(s["name"])
                   if s["name"] in STEP_NAMES else len(STEP_NAMES))
    state = job["state"]
    failed = next((s for s in steps if s["state"] == "failed"), None)
    message = None
    if state == "queued":
        label = "Queued"
    elif state == "running":
        current = (next((s for s in steps if s["state"] == "running"), None)
                   or next((s for s in steps if s["state"] == "pending"), None))
        label = RUNNING_LABELS.get(current["name"], "Running") if current else "Running"
    elif state == "done":
        label = "Done"
    elif state == "failed":
        label = f"Failed at {failed['name']}" if failed else "Failed"
        message = failed.get("message") if failed else None
    else:
        label = state.capitalize()
    return {
        "title": title,
        "label": label,
        "state": state,
        "polling": state in POLLING_STATES,
        "failed_step": failed["name"] if failed else None,
        "message": message,
        "spend": spend,
        "transcript_ready": any(s["name"] == "transcribe" and s["state"] == "done"
                                for s in steps),
        "steps": [{"name": s["name"], "state": s["state"], "message": s.get("message")
                   if s["state"] in ("skipped", "failed") else None,
                   "spend": spend["by_step"].get(s["name"])} for s in steps],
    }
