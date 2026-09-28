"""Zones, lines and access policies (camera-specific)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from ..analytics.geometry import validate_geometry
from ..analytics.policy import validate_schedule
from ..core.errors import bad_request, not_found
from ..db import get_db
from ..deps import client_ip, get_camera_for, require
from ..models import Camera, User, Zone, ZonePolicy
from ..schemas import PolicyIn, ZoneIn, ZoneOut, ZonePatch
from ..services.audit import audit

router = APIRouter(tags=["zones"])

ZONE_CONFIG_KEYS = {
    "polygon": {"min_persistence", "min_seconds", "exit_persistence", "require_entry_transition",
                "require_motion", "motion_min_frac", "min_track_confidence", "object_types", "dwell_seconds",
                "crowd_threshold", "crowd_seconds", "anchor"},
    "line": {"in_side", "deadband_frac", "count_classes", "count_once", "anchor", "purpose"},
}


def _validate(zone_type: str, points: list, config: dict, policies: list[PolicyIn] | None) -> str:
    shape = "line" if zone_type == "line" else "polygon"
    problems = validate_geometry(shape, points)
    unknown = set(config or {}) - ZONE_CONFIG_KEYS[shape]
    if unknown:
        problems.append(f"unknown config keys for a {shape}: {', '.join(sorted(unknown))}")
    if shape == "line" and policies:
        problems.append("lines count crossings; policies belong on polygon zones")
    for i, p in enumerate(policies or []):
        for msg in validate_schedule([w.model_dump() for w in p.schedule]):
            problems.append(f"policy {i + 1}: {msg}")
    if problems:
        raise bad_request("Zone configuration is invalid", problems)
    return shape


def _apply_policies(zone: Zone, policies: list[PolicyIn]) -> None:
    zone.policies = [ZonePolicy(name=p.name, object_types=p.object_types,
                                schedule=[w.model_dump() for w in p.schedule],
                                authorized_roles_or_conditions=p.authorized_roles_or_conditions,
                                action=p.action, severity=p.severity, enabled=p.enabled) for p in policies]


def _zone_for(user: User, db: Session, zone_id: int) -> Zone:
    z = db.get(Zone, zone_id)
    if z is None or not user.can_see_camera(z.camera_id):
        raise not_found("Zone")
    return z


def _touch_camera(db: Session, camera_id: int) -> None:
    cam = db.get(Camera, camera_id)
    if cam:
        cam.config_version += 1


@router.get("/cameras/{camera_id}/zones", response_model=list[ZoneOut])
def list_zones(camera_id: int, user: User = Depends(require("zones:view")), db: Session = Depends(get_db)):
    cam = get_camera_for(user, db, camera_id)
    return db.query(Zone).filter(Zone.camera_id == cam.id).order_by(Zone.id).all()


@router.post("/cameras/{camera_id}/zones", response_model=ZoneOut, status_code=201)
def create_zone(camera_id: int, body: ZoneIn, request: Request, user: User = Depends(require("zones:manage")),
                db: Session = Depends(get_db)):
    cam = get_camera_for(user, db, camera_id)
    shape = _validate(body.zone_type, body.points, body.config, body.policies)
    z = Zone(camera_id=cam.id, name=body.name, zone_type=body.zone_type, shape=shape,
             geometry={"points": [[round(x, 5), round(y, 5)] for x, y in body.points]},
             enabled=body.enabled, severity=body.severity, default_action=body.default_action,
             config=body.config, created_by=user.id)
    _apply_policies(z, body.policies)
    db.add(z)
    _touch_camera(db, cam.id)
    db.flush()
    audit(db, user, "zone.create", "zone", z.id, {"camera_id": cam.id, "name": z.name, "type": z.zone_type},
          client_ip(request))
    db.refresh(z)
    return z


@router.get("/zones/{zone_id}", response_model=ZoneOut)
def get_zone(zone_id: int, user: User = Depends(require("zones:view")), db: Session = Depends(get_db)):
    return _zone_for(user, db, zone_id)


@router.patch("/zones/{zone_id}", response_model=ZoneOut)
def update_zone(zone_id: int, body: ZonePatch, request: Request, user: User = Depends(require("zones:manage")),
                db: Session = Depends(get_db)):
    z = _zone_for(user, db, zone_id)
    zone_type = body.zone_type or z.zone_type
    points = body.points if body.points is not None else z.geometry.get("points", [])
    config = body.config if body.config is not None else (z.config or {})
    shape = _validate(zone_type, points, config, body.policies if body.policies is not None else None)
    if (shape == "line") != (z.shape == "line"):
        raise bad_request("A line cannot be turned into a polygon zone (or back); create a new zone instead")
    for k in ("name", "enabled", "severity", "default_action"):
        v = getattr(body, k)
        if v is not None:
            setattr(z, k, v)
    z.zone_type = zone_type
    if body.points is not None:
        z.geometry = {"points": [[round(x, 5), round(y, 5)] for x, y in body.points]}
    if body.config is not None:
        z.config = body.config
    if body.policies is not None:
        _apply_policies(z, body.policies)
    z.version += 1
    _touch_camera(db, z.camera_id)
    audit(db, user, "zone.update", "zone", z.id, {"fields": sorted(body.model_dump(exclude_unset=True))},
          client_ip(request))
    db.refresh(z)
    return z


@router.delete("/zones/{zone_id}")
def delete_zone(zone_id: int, request: Request, user: User = Depends(require("zones:manage")),
                db: Session = Depends(get_db)):
    z = _zone_for(user, db, zone_id)
    cam_id, name = z.camera_id, z.name
    db.delete(z)
    _touch_camera(db, cam_id)
    audit(db, user, "zone.delete", "zone", zone_id, {"camera_id": cam_id, "name": name}, client_ip(request))
    return {"ok": True}
