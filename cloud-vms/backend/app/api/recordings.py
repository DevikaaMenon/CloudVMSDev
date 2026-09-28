"""Recorded segments: browse by camera/time range and play back via signed URLs."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..core.errors import not_found
from ..core.timeutil import utcnow
from ..db import get_db
from ..deps import get_camera_for, require
from ..models import RecordingSegment, User
from ..schemas import RecordingOut
from ..services.storage import get_storage

router = APIRouter(tags=["recordings"])


def _naive_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo else dt


@router.get("/cameras/{camera_id}/recordings")
def list_recordings(camera_id: int, start: Optional[datetime] = None, end: Optional[datetime] = None,
                    limit: int = Query(500, ge=1, le=5000), user: User = Depends(require("recordings:view")),
                    db: Session = Depends(get_db)):
    cam = get_camera_for(user, db, camera_id)
    end_u = _naive_utc(end) or utcnow() + timedelta(minutes=5)
    start_u = _naive_utc(start) or end_u - timedelta(hours=24)
    q = select(RecordingSegment).where(RecordingSegment.camera_id == cam.id,
                                       RecordingSegment.end_ts >= start_u,
                                       RecordingSegment.start_ts <= end_u).order_by(RecordingSegment.start_ts)
    rows = db.scalars(q.limit(limit)).all()
    totals = db.execute(select(func.count(RecordingSegment.id), func.coalesce(func.sum(RecordingSegment.size_bytes), 0),
                               func.coalesce(func.sum(RecordingSegment.duration_s), 0))
                        .where(RecordingSegment.camera_id == cam.id)).one()
    return {"camera_id": cam.id, "items": [RecordingOut.model_validate(r).model_dump() for r in rows],
            "total_segments": totals[0], "total_bytes": int(totals[1]), "total_seconds": float(totals[2])}


def _segment(user: User, db: Session, recording_id: int) -> RecordingSegment:
    seg = db.get(RecordingSegment, recording_id)
    if seg is None or not user.can_see_camera(seg.camera_id):
        raise not_found("Recording")
    return seg


@router.get("/recordings/{recording_id}", response_model=RecordingOut)
def get_recording(recording_id: int, user: User = Depends(require("recordings:view")),
                  db: Session = Depends(get_db)):
    return _segment(user, db, recording_id)


@router.get("/recordings/{recording_id}/playback")
def playback(recording_id: int, user: User = Depends(require("recordings:view")), db: Session = Depends(get_db)):
    seg = _segment(user, db, recording_id)
    if seg.status == "failed":
        raise not_found("Recording file")
    return {"url": get_storage().signed_url(seg.object_key), "media_type": "video/mp4",
            "start_ts": RecordingOut.model_validate(seg).model_dump()["start_ts"], "duration_s": seg.duration_s}
