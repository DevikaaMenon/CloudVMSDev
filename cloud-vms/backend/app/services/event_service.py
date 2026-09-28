"""Incident persistence, status workflow and notifications."""
from __future__ import annotations

import json
import logging
import threading
import urllib.request
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..analytics.event_engine import Decision, escalate
from ..analytics.policy import SEVERITIES
from ..analytics.rules import RULE_VERSION
from ..core.config import get_settings
from ..core.errors import bad_request
from ..core.timeutil import from_epoch, iso_z, utcnow
from ..db import session_scope
from ..models import Event, NotificationDelivery, User

log = logging.getLogger("vms.events")

STATUSES = ("NEW", "ACKNOWLEDGED", "INVESTIGATING", "RESOLVED", "DISMISSED")
TRANSITIONS = {
    "NEW": {"ACKNOWLEDGED", "INVESTIGATING", "RESOLVED", "DISMISSED"},
    "ACKNOWLEDGED": {"INVESTIGATING", "RESOLVED", "DISMISSED"},
    "INVESTIGATING": {"ACKNOWLEDGED", "RESOLVED", "DISMISSED"},
    "RESOLVED": {"INVESTIGATING"},  # re-open
    "DISMISSED": {"INVESTIGATING"},
}

EVENT_TYPES = ("INTRUSION_DETECTED", "RESTRICTED_AREA_ACCESS", "LOITERING_DETECTED", "CROWD_DETECTED",
               "CAMERA_OFFLINE", "RECORDING_FAILURE")


def persist_decision(db: Session, camera_id: int, decision: Decision, model_version: str,
                     escalate_at: int = 3) -> tuple[Event | None, bool]:
    """Insert a new incident or merge an extra object into an open one. Returns (event, is_new)."""
    c = decision.candidate
    obj = {"track_id": c.track_uid, "root_id": c.root_uid, "class": c.cls,
           "confidence": round(c.confidence, 3), "ts": iso_z(from_epoch(c.ts)),
           "bbox": [round(v, 1) for v in c.bbox] if c.bbox else None}
    if decision.kind == "merge" and decision.merge_into:
        ev = db.scalar(select(Event).where(Event.dedup_key == decision.merge_into))
        if ev is None:
            return None, False
        meta = dict(ev.metadata_ or {})
        objects = list(meta.get("objects", []))
        objects.append(obj)
        meta["objects"] = objects
        meta["object_count"] = len(objects)
        ev.metadata_ = meta
        if len(objects) >= escalate_at and ev.severity == c.severity:
            ev.severity = escalate(ev.severity)
        db.commit()
        return ev, False
    if decision.kind != "new":
        return None, False
    ev = Event(camera_id=camera_id, zone_id=c.zone_id, track_id=c.track_uid, event_type=c.event_type,
               object_type=c.cls, event_timestamp=from_epoch(c.ts), severity=c.severity,
               confidence=round(c.confidence, 3), bounding_box=list(c.bbox) if c.bbox else None,
               title=c.title, dedup_key=decision.dedup_key, rule_version=RULE_VERSION,
               model_version=model_version,
               metadata_={**c.metadata, "policy_id": c.policy_id, "objects": [obj], "object_count": 1})
    db.add(ev)
    try:
        db.commit()
    except IntegrityError:  # duplicate delivery -> idempotent
        db.rollback()
        return db.scalar(select(Event).where(Event.dedup_key == decision.dedup_key)), False
    return ev, True


def create_system_event(db: Session, camera_id: int, event_type: str, title: str, severity: str,
                        dedup_key: str, metadata: dict | None = None) -> Event | None:
    ev = Event(camera_id=camera_id, event_type=event_type, object_type="system", event_timestamp=utcnow(),
               severity=severity, title=title, dedup_key=dedup_key, metadata_=metadata or {},
               rule_version="system")
    db.add(ev)
    try:
        db.commit()
        return ev
    except IntegrityError:
        db.rollback()
        return None


def auto_resolve(db: Session, dedup_key: str, note: str) -> None:
    ev = db.scalar(select(Event).where(Event.dedup_key == dedup_key))
    if ev and ev.status in ("NEW", "ACKNOWLEDGED", "INVESTIGATING"):
        ev.status, ev.resolved_at = "RESOLVED", utcnow()
        ev.notes = (ev.notes + "\n" if ev.notes else "") + note
        db.commit()


def transition(db: Session, ev: Event, new_status: str, user: User, note: str = "") -> Event:
    new_status = new_status.upper()
    if new_status not in STATUSES:
        raise bad_request(f"unknown status {new_status}")
    if new_status == ev.status:
        return ev
    if new_status not in TRANSITIONS[ev.status]:
        raise bad_request(f"cannot move an incident from {ev.status} to {new_status}")
    now = utcnow()
    if new_status == "ACKNOWLEDGED" or (new_status in ("INVESTIGATING", "RESOLVED", "DISMISSED")
                                        and ev.acknowledged_at is None):
        ev.acknowledged_by, ev.acknowledged_at = user.id, now
    if new_status in ("RESOLVED", "DISMISSED"):
        ev.resolved_by, ev.resolved_at = user.id, now
    if new_status == "INVESTIGATING" and ev.status in ("RESOLVED", "DISMISSED"):
        ev.resolved_by, ev.resolved_at = None, None
    ev.status = new_status
    if note:
        stamp = now.strftime("%Y-%m-%d %H:%M UTC")
        ev.notes = (ev.notes + "\n" if ev.notes else "") + f"[{stamp}] {user.username}: {note}"
    db.commit()
    return ev


# ------------------------------------------------------------------ notifications
def notify_async(event_id: int) -> None:
    s = get_settings()
    if not s.notify_webhook_url:
        return
    threading.Thread(target=_deliver_webhook, args=(event_id,), daemon=True).start()


def _deliver_webhook(event_id: int) -> None:
    s = get_settings()
    with session_scope() as db:
        ev = db.get(Event, event_id)
        if ev is None or SEVERITIES.index(ev.severity) < SEVERITIES.index(s.notify_min_severity):
            return
        delivery = NotificationDelivery(event_id=ev.id, channel="webhook", target=s.notify_webhook_url)
        db.add(delivery)
        db.flush()
        payload = json.dumps({"event_id": ev.id, "type": ev.event_type, "title": ev.title,
                              "severity": ev.severity, "camera_id": ev.camera_id,
                              "timestamp": iso_z(ev.event_timestamp)}).encode()
        for attempt in range(1, 4):
            delivery.attempts = attempt
            try:
                req = urllib.request.Request(s.notify_webhook_url, data=payload,
                                             headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=5):
                    pass
                delivery.status, delivery.error = "sent", ""
                break
            except Exception as exc:
                delivery.status, delivery.error = "failed", str(exc)[:300]
                log.warning("webhook delivery for event %s failed (attempt %d): %s", ev.id, attempt, exc)
                threading.Event().wait(2 * attempt)


def retention_date(days: int):
    return utcnow() + timedelta(days=days)
