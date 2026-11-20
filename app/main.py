from __future__ import annotations
"""DocFlow API service. Stands in for API Gateway + the API Lambda."""
import os
import re
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .cache import TTLCache
from .config import Settings
from .db import Database, Jobs
from .events import EventBus, make_audit_log, make_notifier
from .queue import Queue
from .worker import Processor

ALLOWED_EXT = {".txt", ".csv", ".pdf"}
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
try:
    VERSION = (Path(__file__).resolve().parent.parent / "VERSION").read_text().strip()
except OSError:
    VERSION = "dev"


class Services:
    """Wires the components together. Each one only knows its neighbours' interfaces."""

    def __init__(self, settings: Settings):
        self.settings = settings
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        (settings.data_dir / "uploads").mkdir(exist_ok=True)  # the "S3 bucket"
        self.db = Database(settings.data_dir / "docflow.db")
        self.cache = TTLCache(settings.cache_ttl)
        self.jobs = Jobs(self.db, self.cache)
        self.queue = Queue(self.db, settings.visibility_timeout, settings.max_receives)
        self.bus = EventBus()
        notifier = make_notifier(self.db)
        audit = make_audit_log(settings.data_dir / "events.log")
        for topic in ("JobCompleted", "JobFailed"):
            self.bus.subscribe(topic, notifier)
            self.bus.subscribe(topic, audit)


class JobRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=200)


def create_app(settings: Settings | None = None) -> FastAPI:
    svc = Services(settings or Settings.from_env())

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        worker = Processor(svc)
        worker.start()
        yield
        worker.stop()
        worker.join(timeout=5)

    app = FastAPI(title="DocFlow", lifespan=lifespan)
    app.state.svc = svc

    def current_user(authorization: str = Header(default="")) -> str:
        """Cognito stand-in: 'Authorization: Bearer demo:<username>'."""
        if authorization.startswith("Bearer demo:"):
            user = authorization[len("Bearer demo:"):].strip()
            if re.fullmatch(r"[a-z0-9_-]{1,32}", user):
                return user
        raise HTTPException(401, "Send 'Authorization: Bearer demo:<username>' (stand-in for a Cognito JWT)")

    def admin_user(user: str = Depends(current_user)) -> str:
        if user != "admin":
            raise HTTPException(403, "admin only")
        return user

    @app.get("/health")
    def health():
        return {"ok": True, "version": VERSION}

    @app.post("/jobs", status_code=201)
    def create_job(req: JobRequest, user: str = Depends(current_user)):
        name = os.path.basename(req.filename.replace("\\", "/"))
        name = re.sub(r"[^A-Za-z0-9._-]", "_", name)
        if Path(name).suffix.lower() not in ALLOWED_EXT:
            raise HTTPException(400, f"Unsupported file type. Allowed: {', '.join(sorted(ALLOWED_EXT))}")
        token = secrets.token_urlsafe(24)
        job_id = svc.jobs.create(user, name, token, svc.settings.upload_url_ttl)
        return {"job_id": job_id, "upload_url": f"/uploads/{token}", "expires_in": svc.settings.upload_url_ttl}

    @app.put("/uploads/{token}", status_code=204)
    async def upload(token: str, request: Request):
        """Pre-signed URL stand-in. The token is the only credential, single use."""
        job = svc.jobs.by_token(token)
        if job is None or job["status"] != "AWAITING_UPLOAD" or job["upload_expires"] < time.time():
            raise HTTPException(403, "Upload link is invalid or expired")
        path = svc.settings.data_dir / "uploads" / f"{job['id']}_{job['filename']}"
        size = 0
        too_big = False
        with open(path, "wb") as f:
            async for chunk in request.stream():
                size += len(chunk)
                if size > svc.settings.max_upload_bytes:
                    too_big = True
                    break
                f.write(chunk)
        if too_big:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            raise HTTPException(413, "File is too large")
        svc.jobs.mark_uploaded(job["id"], str(path), size)
        # S3 ObjectCreated event notification -> SQS
        svc.queue.send({"event": "ObjectCreated", "job_id": job["id"], "key": path.name})
        return Response(status_code=204)

    @app.get("/jobs")
    def list_jobs(user: str = Depends(current_user)):
        return [_strip(Jobs.public(r)) for r in svc.jobs.list_for(user)]

    @app.get("/jobs/{job_id}")
    def get_job(job_id: str, response: Response, user: str = Depends(current_user)):
        key = f"job:{job_id}"
        cached = svc.cache.get(key)
        if cached is not None:
            response.headers["X-Cache"] = "HIT"
            job = cached
        else:
            row = svc.jobs.row(job_id)
            if row is None:
                raise HTTPException(404, "Job not found")
            job = Jobs.public(row)
            svc.cache.set(key, job)
            response.headers["X-Cache"] = "MISS"
        if job["user"] != user:
            raise HTTPException(404, "Job not found")
        return _strip(job)

    @app.get("/notifications")
    def notifications(user: str = Depends(current_user)):
        rows = svc.db.query(
            "SELECT subject, body, created_at FROM emails WHERE user=? ORDER BY id DESC LIMIT 50", (user,)
        )
        return [dict(r) for r in rows]

    @app.get("/admin/queue")
    def queue_stats(_: str = Depends(admin_user)):
        return svc.queue.stats()

    @app.post("/admin/dlq/replay")
    def replay(_: str = Depends(admin_user)):
        return {"replayed": svc.queue.replay_dead_letters()}

    @app.get("/")
    def index():
        return FileResponse(STATIC_DIR / "index.html")

    return app


def _strip(job: dict) -> dict:
    return {k: v for k, v in job.items() if k != "user"}


app = create_app()
