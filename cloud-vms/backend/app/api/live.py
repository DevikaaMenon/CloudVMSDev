"""Live preview (MJPEG), live incident feed (Server-Sent Events) and signed media downloads."""
from __future__ import annotations

import asyncio
import json
import mimetypes
import time

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from sqlalchemy import select

from ..core.errors import AppError, not_found
from ..core.security import create_stream_token, verify_media_signature
from ..db import SessionLocal
from ..deps import get_current_user, require, stream_user
from ..models import Camera, Event, User
from ..services.storage import InvalidKey, get_storage, validate_key
from ..workers.framebus import get_frame_bus

router = APIRouter(tags=["live"])


def _placeholder_jpeg(text: str) -> bytes:
    import cv2
    import numpy as np
    img = np.full((360, 640, 3), 30, dtype=np.uint8)
    cv2.putText(img, text, (30, 190), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (200, 200, 200), 2, cv2.LINE_AA)
    return cv2.imencode(".jpg", img)[1].tobytes()


@router.get("/live/{camera_id}/mjpeg")
async def mjpeg(camera_id: int, request: Request, token: str = Query(...)):
    db = SessionLocal()
    try:
        user = stream_user(token, f"live:{camera_id}", db)
        if "live:view" not in user.permissions or not user.can_see_camera(camera_id):
            raise AppError(403, "forbidden", "No access to this camera")
        cam = db.get(Camera, camera_id)
        if cam is None:
            raise not_found("Camera")
    finally:
        db.close()
    bus = get_frame_bus()
    boundary = "frame"

    async def gen():
        last = 0.0
        idle_since = time.time()
        while not await request.is_disconnected():
            item = await asyncio.to_thread(bus.wait_newer, camera_id, last, 1.0)
            if item and item[0] > last:
                last = item[0]
                idle_since = time.time()
                jpeg = item[1]
            elif time.time() - idle_since > 3:
                jpeg = _placeholder_jpeg("waiting for video...")
                idle_since = time.time()
            else:
                continue
            yield (f"--{boundary}\r\nContent-Type: image/jpeg\r\nContent-Length: {len(jpeg)}\r\n\r\n").encode() \
                + jpeg + b"\r\n"

    return StreamingResponse(gen(), media_type=f"multipart/x-mixed-replace; boundary={boundary}",
                             headers={"Cache-Control": "no-store"})


@router.post("/events/stream-token")
def events_stream_token(user: User = Depends(require("events:view"))):
    return {"token": create_stream_token(user.id, user.token_version, "events")}


@router.get("/events/stream")
async def events_stream(request: Request, token: str = Query(...)):
    """Server-Sent Events: pushes new incidents to the dashboard as they are created."""
    db = SessionLocal()
    try:
        user = stream_user(token, "events", db)
        if "events:view" not in user.permissions:
            raise AppError(403, "forbidden", "No access to events")
        scope = user.camera_scope
        last_id = db.scalar(select(Event.id).order_by(Event.id.desc()).limit(1)) or 0
    finally:
        db.close()

    def fetch(after: int):
        s = SessionLocal()
        try:
            q = select(Event, Camera.name).join(Camera, Camera.id == Event.camera_id).where(Event.id > after)
            if scope is not None:
                q = q.where(Event.camera_id.in_(scope or [-1]))
            rows = s.execute(q.order_by(Event.id).limit(50)).all()
            from ..core.timeutil import iso_z
            return [{"id": e.id, "camera_id": e.camera_id, "camera_name": name, "event_type": e.event_type,
                     "title": e.title, "severity": e.severity, "status": e.status,
                     "event_timestamp": iso_z(e.event_timestamp)} for e, name in rows]
        finally:
            s.close()

    async def gen():
        nonlocal last_id
        yield "retry: 3000\n\n"
        ticks = 0
        while not await request.is_disconnected():
            items = await asyncio.to_thread(fetch, last_id)
            for it in items:
                last_id = max(last_id, it["id"])
                yield f"event: incident\ndata: {json.dumps(it)}\n\n"
            ticks += 1
            if ticks % 15 == 0:
                yield ": keep-alive\n\n"
            await asyncio.sleep(1.0)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


@router.get("/media/{key:path}")
def media(key: str, request: Request, exp: int = Query(...), sig: str = Query(...)):
    """Serves local-storage objects behind a short-lived HMAC signature (with HTTP Range support)."""
    try:
        validate_key(key)
    except InvalidKey:
        raise not_found("Object")
    if not verify_media_signature(key, exp, sig):
        raise AppError(403, "link_expired", "This media link has expired, reload the page")
    path = get_storage().local_path(key)
    if path is None or not path.exists():
        raise not_found("Object")
    ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    size = path.stat().st_size
    rng = request.headers.get("range")
    if rng and rng.startswith("bytes="):
        start_s, _, end_s = rng[6:].split(",")[0].strip().partition("-")
        try:
            start = int(start_s) if start_s else max(0, size - int(end_s))
            end = min(int(end_s), size - 1) if end_s and start_s else size - 1
        except ValueError:  # malformed header, e.g. "bytes=abc-" or "bytes=-"
            start, end = size, -1
        if start < 0 or start >= size or start > end:
            return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
        end = min(end, start + 8 * 1024 * 1024 - 1)  # serve large files in 8 MB chunks
        with open(path, "rb") as f:
            f.seek(start)
            data = f.read(end - start + 1)
        return Response(data, status_code=206, media_type=ctype, headers={
            "Content-Range": f"bytes {start}-{end}/{size}", "Accept-Ranges": "bytes",
            "Content-Length": str(len(data)), "Cache-Control": "private, max-age=300"})
    return FileResponse(path, media_type=ctype, headers={"Accept-Ranges": "bytes",
                                                         "Cache-Control": "private, max-age=300"})


_ = get_current_user  # re-exported for symmetry with other routers
