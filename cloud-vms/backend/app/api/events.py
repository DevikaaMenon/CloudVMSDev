"""Incident search, details, workflow and evidence."""
from __future__ import annotations

import csv
import io
from datetime import datetime
from typing import Literal, Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import asc, desc, func, or_, select
from sqlalchemy.orm import Session

from ..core.errors import not_found
from ..core.timeutil import iso_z
from ..db import get_db
from ..deps import client_ip, require, scoped_camera_ids
from ..models import Camera, Event, User, Zone
from ..schemas import EventOut, EventPatch, EvidenceOut, Page
from ..services.audit import audit
from ..services.event_service import transition
from ..services.storage import get_storage

router = APIRouter(tags=["events"])

SORTABLE = {"event_timestamp": Event.event_timestamp, "severity": Event.severity, "status": Event.status,
            "event_type": Event.event_type, "camera_id": Event.camera_id, "id": Event.id}


def _event_out(ev: Event, camera_name: str = "", zone_name: str = "") -> EventOut:
    out = EventOut.model_validate(ev)
    out.camera_name, out.zone_name = camera_name, zone_name or ""
    out.has_snapshot = bool(ev.snapshot_object_key)
    out.has_clip = bool(ev.video_clip_object_key)
    return out


def _filtered(user: User, camera_id, event_type, severity, status, zone_id, object_type, start, end, q):
    stmt = select(Event, Camera.name, Zone.name).join(Camera, Camera.id == Event.camera_id) \
        .outerjoin(Zone, Zone.id == Event.zone_id)
    scope = scoped_camera_ids(user)
    if scope is not None:
        stmt = stmt.where(Event.camera_id.in_(scope or [-1]))
    if camera_id:
        stmt = stmt.where(Event.camera_id.in_(camera_id))
    if event_type:
        stmt = stmt.where(Event.event_type.in_(event_type))
    if severity:
        stmt = stmt.where(Event.severity.in_(severity))
    if status:
        stmt = stmt.where(Event.status.in_(status))
    if zone_id:
        stmt = stmt.where(Event.zone_id.in_(zone_id))
    if object_type:
        stmt = stmt.where(Event.object_type.in_(object_type))
    if start:  # already converted to naive UTC by _utc()
        stmt = stmt.where(Event.event_timestamp >= start)
    if end:
        stmt = stmt.where(Event.event_timestamp < end)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(Event.title.ilike(like), Event.notes.ilike(like)))
    return stmt


def _utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    from datetime import timezone
    return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo else dt


@router.get("/events", response_model=Page[EventOut])
def list_events(
    camera_id: list[int] = Query(default=[]), event_type: list[str] = Query(default=[]),
    severity: list[str] = Query(default=[]), status: list[str] = Query(default=[]),
    zone_id: list[int] = Query(default=[]), object_type: list[str] = Query(default=[]),
    start: Optional[datetime] = None, end: Optional[datetime] = None, q: str = "",
    sort: str = "event_timestamp", order: Literal["asc", "desc"] = "desc",
    page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=200),
    user: User = Depends(require("events:view")), db: Session = Depends(get_db),
):
    stmt = _filtered(user, camera_id, event_type, severity, status, zone_id, object_type, _utc(start), _utc(end), q)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    col = SORTABLE.get(sort, Event.event_timestamp)
    stmt = stmt.order_by(desc(col) if order == "desc" else asc(col), desc(Event.id))
    rows = db.execute(stmt.offset((page - 1) * page_size).limit(page_size)).all()
    return Page(items=[_event_out(e, cn, zn) for e, cn, zn in rows], total=total, page=page, page_size=page_size)


@router.get("/events/export.csv")
def export_events(
    camera_id: list[int] = Query(default=[]), event_type: list[str] = Query(default=[]),
    severity: list[str] = Query(default=[]), status: list[str] = Query(default=[]),
    start: Optional[datetime] = None, end: Optional[datetime] = None,
    user: User = Depends(require("events:view")), db: Session = Depends(get_db),
):
    stmt = _filtered(user, camera_id, event_type, severity, status, [], [], _utc(start), _utc(end), "")
    rows = db.execute(stmt.order_by(desc(Event.event_timestamp)).limit(50000)).all()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["id", "timestamp_utc", "camera", "zone", "type", "object", "severity", "status", "confidence",
                "objects", "title"])
    for e, cn, zn in rows:
        w.writerow([e.id, iso_z(e.event_timestamp), cn, zn or "", e.event_type, e.object_type, e.severity, e.status,
                    e.confidence, (e.metadata_ or {}).get("object_count", 1), e.title])
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=incidents.csv"})


def _get(user: User, db: Session, event_id: int):
    row = db.execute(select(Event, Camera.name, Zone.name).join(Camera, Camera.id == Event.camera_id)
                     .outerjoin(Zone, Zone.id == Event.zone_id).where(Event.id == event_id)).first()
    if row is None or not user.can_see_camera(row[0].camera_id):
        raise not_found("Incident")
    return row


@router.get("/events/{event_id:int}", response_model=EventOut)
def get_event(event_id: int, user: User = Depends(require("events:view")), db: Session = Depends(get_db)):
    e, cn, zn = _get(user, db, event_id)
    return _event_out(e, cn, zn)


@router.patch("/events/{event_id:int}", response_model=EventOut)
def update_event(event_id: int, body: EventPatch, request: Request, user: User = Depends(require("events:update")),
                 db: Session = Depends(get_db)):
    e, cn, zn = _get(user, db, event_id)
    before = e.status
    if body.severity and body.severity != e.severity:
        e.severity = body.severity
    if body.status:
        transition(db, e, body.status, user, body.note)
    elif body.note:
        from ..core.timeutil import utcnow
        e.notes = (e.notes + "\n" if e.notes else "") + f"[{utcnow():%Y-%m-%d %H:%M} UTC] {user.username}: {body.note}"
    db.commit()
    audit(db, user, "event.update", "event", e.id, {"from": before, "to": e.status, "severity": e.severity},
          client_ip(request))
    return _event_out(e, cn, zn)


@router.post("/events/{event_id:int}/acknowledge", response_model=EventOut)
def acknowledge(event_id: int, request: Request, user: User = Depends(require("events:update")),
                db: Session = Depends(get_db)):
    return update_event(event_id, EventPatch(status="ACKNOWLEDGED"), request, user, db)


@router.post("/events/{event_id:int}/resolve", response_model=EventOut)
def resolve(event_id: int, request: Request, body: EventPatch | None = None,
            user: User = Depends(require("events:update")), db: Session = Depends(get_db)):
    return update_event(event_id, EventPatch(status="RESOLVED", note=(body.note if body else "")), request, user, db)


@router.get("/events/{event_id:int}/evidence", response_model=list[EvidenceOut])
def evidence(event_id: int, user: User = Depends(require("events:view")), db: Session = Depends(get_db)):
    e, _, _ = _get(user, db, event_id)
    storage = get_storage()
    out = []
    for ev in sorted(e.evidence, key=lambda x: x.kind, reverse=True):
        item = EvidenceOut.model_validate(ev)
        if ev.object_key and ev.upload_status == "uploaded":
            item.url = storage.signed_url(ev.object_key)  # short-lived, per-request link
        out.append(item)
    return out
