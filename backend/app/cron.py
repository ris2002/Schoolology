"""Scheduled jobs over HTTP, for schedulers that call a URL instead of running a command
(Render Cron, cron-job.org, GitHub Actions, ...).

    POST /cron/sync           every few hours
    POST /cron/weekly         once a week
    POST /cron/cleanup-demo   daily
    Header: Authorization: Bearer <CRON_SECRET>

The job runs after the reply is sent, so a scheduler with a short timeout still works.
Only one job runs at a time. GET /healthz is a cheap page for uptime checks.
"""

import hmac
import logging
import threading

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException

from . import config, jobs

log = logging.getLogger("cron")
router = APIRouter()
JOBS = ("sync", "weekly", "cleanup-demo")
_running = threading.Lock()


def _run(name: str) -> None:
    try:
        jobs.COMMANDS[name]()
        log.info("Cron job %s finished", name)
    except Exception:
        log.exception("Cron job %s failed", name)
    finally:
        _running.release()


@router.post("/cron/{name}", status_code=202)
def run_job(name: str, background: BackgroundTasks, authorization: str = Header("")):
    if not config.CRON_SECRET:
        raise HTTPException(503, "CRON_SECRET is not set")
    if not hmac.compare_digest(authorization.encode(), f"Bearer {config.CRON_SECRET}".encode()):
        raise HTTPException(401, "Wrong or missing cron secret")
    if name not in JOBS:
        raise HTTPException(404, f"Unknown job. Choose one of: {', '.join(JOBS)}")
    if not _running.acquire(blocking=False):
        raise HTTPException(409, "Another job is still running")
    background.add_task(_run, name)
    return {"started": name}


@router.get("/healthz", include_in_schema=False)
def health():
    return {"ok": True}
