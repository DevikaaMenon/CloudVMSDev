"""Camera helpers shared by the API and the workers."""
from __future__ import annotations

import hashlib
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..analytics.pipeline import AnalyticsConfig
from ..analytics.policy import PolicySpec
from ..analytics.rules import ZoneSpec
from ..core.security import decrypt_secret
from ..models import Camera, Zone
from ..workers.sources import build_url

STREAM_TYPES = ("file", "rtsp", "http", "webcam")
CAMERA_STATUSES = ("REGISTERED", "ONLINE", "OFFLINE", "ERROR", "DISABLED")


def default_analytics_config() -> dict:
    return AnalyticsConfig().to_dict()


def merged_analytics(cam: Camera) -> AnalyticsConfig:
    return AnalyticsConfig.from_dict({**default_analytics_config(), **(cam.analytics_config or {})})


def zone_to_spec(z: Zone) -> ZoneSpec:
    return ZoneSpec(
        id=z.id, camera_id=z.camera_id, name=z.name, zone_type=z.zone_type, shape=z.shape,
        points=(z.geometry or {}).get("points", []), severity=z.severity, default_action=z.default_action,
        config=z.config or {}, enabled=z.enabled, version=z.version,
        policies=[PolicySpec(p.id, p.object_types or [], p.schedule or [], p.action, p.severity, p.enabled, p.name)
                  for p in z.policies])


def camera_zones(db: Session, camera_id: int) -> list[Zone]:
    return list(db.scalars(select(Zone).where(Zone.camera_id == camera_id).order_by(Zone.id)))


def zones_signature(zones: list[Zone]) -> str:
    raw = json.dumps([[z.id, z.version, z.enabled, str(z.updated_at),
                       [[p.id, p.enabled, p.action, p.object_types, p.schedule] for p in z.policies]]
                      for z in zones], default=str)
    return hashlib.sha1(raw.encode()).hexdigest()


def stream_url(cam: Camera) -> str:
    user = pwd = ""
    if cam.credential:
        user = decrypt_secret(cam.credential.username_enc) if cam.credential.username_enc else ""
        pwd = decrypt_secret(cam.credential.password_enc) if cam.credential.password_enc else ""
    return build_url(cam.stream_type, cam.stream_reference, user, pwd)


def runtime_config(db: Session, cam: Camera):
    from ..workers.camera_worker import CameraRuntimeConfig
    zones = camera_zones(db, cam.id)
    # Zones are always filtered by *this* camera id: one camera's zones can never leak to another.
    specs = [zone_to_spec(z) for z in zones if z.camera_id == cam.id]
    return CameraRuntimeConfig(camera_id=cam.id, name=cam.name, stream_type=cam.stream_type, url=stream_url(cam),
                               analytics_enabled=cam.analytics_enabled, analytics=merged_analytics(cam),
                               zones=specs, config_version=cam.config_version,
                               zones_signature=zones_signature(zones))
