"""Cloud VMS — FastAPI application entry point.

    uvicorn app.main:app --host 0.0.0.0 --port 8000        (from the backend/ folder)
"""
from __future__ import annotations

import logging
import secrets
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select, update

from .api import admin, analytics, auth, cameras, events, live, models_api, recordings, zones
from .core.config import get_settings
from .core.errors import install_error_handlers
from .core.logging import request_id_var, setup_logging
from .core.permissions import ROLE_PERMISSIONS
from .core.security import hash_password
from .core.timeutil import utcnow
from .db import init_db, session_scope
from .models import Dataset, Role, TrainingJob, User
from .services.model_registry import seed_models

log = logging.getLogger("vms")


def bootstrap() -> None:
    """Create tables, roles, the first administrator and the model registry (idempotent)."""
    s = get_settings()
    init_db()
    with session_scope() as db:
        existing = {r.name: r for r in db.scalars(select(Role))}
        for name, spec in ROLE_PERMISSIONS.items():
            if name in existing:
                existing[name].permissions = spec["permissions"]  # keep matrix in sync with code
                existing[name].description = spec["description"]
            else:
                db.add(Role(name=name, description=spec["description"], permissions=spec["permissions"]))
        db.flush()
        if db.scalar(select(User).limit(1)) is None:
            password = s.admin_password or secrets.token_urlsafe(9)
            admin_role = db.scalar(select(Role).where(Role.name == "admin"))
            db.add(User(username=s.admin_username, full_name="Administrator", password_hash=hash_password(password),
                        roles=[admin_role], must_change_password=not s.admin_password))
            if not s.admin_password:
                (s.data_dir / "initial_admin_password.txt").write_text(
                    f"username: {s.admin_username}\npassword: {password}\n"
                    "You will be asked to change it after signing in (or use Password at the bottom of the side menu).\n")
            log.warning("created administrator '%s' — password in %s", s.admin_username,
                        "VMS_ADMIN_PASSWORD" if s.admin_password else "data/initial_admin_password.txt")
    with session_scope() as db:
        seed_models(db)
    with session_scope() as db:
        # training / dataset imports run as threads of the API process: anything still marked as
        # running at start-up died with the previous process and would block new jobs forever
        stale = db.execute(update(TrainingJob).where(TrainingJob.status.in_(["queued", "running"]))
                           .values(status="failed", finished_at=utcnow())).rowcount
        stale += db.execute(update(Dataset).where(Dataset.status == "processing")
                            .values(status="failed", stats={"error": "interrupted by a server restart"})).rowcount
        if stale:
            log.warning("marked %d interrupted training job(s) / dataset import(s) as failed", stale)


@asynccontextmanager
async def lifespan(_: FastAPI):
    s = get_settings()
    setup_logging(json_logs=s.environment == "production")
    bootstrap()
    if s.embedded_workers:
        from .workers.supervisor import start_supervisor
        start_supervisor()
        log.info("embedded camera workers started (group '%s')", s.worker_group)
    yield
    if s.embedded_workers:
        from .workers.supervisor import stop_supervisor
        stop_supervisor()


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(title="Cloud VMS API", version="1.0.0", lifespan=lifespan,
                  description="Cloud-Based Video Management System with Automated Incident Detection and "
                              "Scalable Video Analytics", docs_url="/api/docs", openapi_url="/api/openapi.json")
    install_error_handlers(app)
    app.add_middleware(CORSMiddleware, allow_origins=s.cors_origins, allow_credentials=False,
                       allow_methods=["*"], allow_headers=["*"], expose_headers=["X-Request-ID"])

    @app.middleware("http")
    async def correlation_id(request: Request, call_next):
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        token = request_id_var.set(rid)
        t0 = time.perf_counter()
        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(token)
        response.headers["X-Request-ID"] = rid
        response.headers["X-Response-Time-ms"] = f"{(time.perf_counter() - t0) * 1000:.1f}"
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        return response

    for r in (auth.router, cameras.router, live.router, zones.router, events.router, recordings.router,
              analytics.router, admin.router, models_api.router):
        app.include_router(r, prefix="/api")

    @app.get("/api/health", tags=["system"])
    def health():
        return {"status": "ok", "app": s.app_name}

    # ---- single-page frontend (built with `npm run build`)
    dist = s.frontend_dist
    if (dist / "index.html").exists():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            if path.startswith("api/"):
                return JSONResponse({"error": {"code": "not_found", "message": "Unknown API route"}}, 404)
            f = (dist / path).resolve()
            if path and f.is_file() and dist.resolve() in f.parents:
                return FileResponse(f)
            return FileResponse(dist / "index.html")
    else:
        @app.get("/", include_in_schema=False)
        def no_frontend():
            return JSONResponse({"message": "API is running. Build the frontend (cd frontend && npm run build) "
                                            "or open /api/docs"})
    return app


app = create_app()
