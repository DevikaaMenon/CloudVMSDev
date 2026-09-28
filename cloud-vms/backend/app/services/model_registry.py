"""Model registry: which detector weights are available and which are active."""
from __future__ import annotations

import logging
from pathlib import Path

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..analytics.detector import ModelSpec, weights_available
from ..core.config import PROJECT_ROOT, get_settings
from ..models import ModelVersion
from .audit import get_setting

log = logging.getLogger("vms.models")


def resolve_weights(weights: str) -> str:
    """Hub names (yolo26n.pt) are cached in data/weights (downloaded there on first use);
    relative paths are relative to the project root."""
    p = Path(weights)
    if p.is_absolute():
        return weights
    if p.parent == Path("."):
        return str(get_settings().weights_dir / p.name)
    return str((PROJECT_ROOT / p).resolve())


def seed_models(db: Session) -> None:
    cfg_path = get_settings().models_config
    if not cfg_path.exists():
        return
    data = yaml.safe_load(cfg_path.read_text()) or {}
    existing = {m.name for m in db.scalars(select(ModelVersion))}
    for m in data.get("models", []):
        if m["name"] in existing:
            continue
        db.add(ModelVersion(name=m["name"], role=m["role"], weights=m["weights"],
                            class_map=m.get("class_map", {}), source=m.get("source", "pretrained"),
                            dataset=m.get("dataset", ""), notes=m.get("notes", ""),
                            is_active=bool(m.get("active", False))))
    db.commit()


def model_is_available(m: ModelVersion) -> bool:
    return weights_available(resolve_weights(m.weights))


def active_specs(db: Session) -> tuple[str, list[ModelSpec]]:
    """Return (profile, model specs) the workers should load right now."""
    profile = get_setting(db, "detector_profile") or "shared"
    models = list(db.scalars(select(ModelVersion)))

    def pick(role: str) -> ModelVersion | None:
        active = [m for m in models if m.role == role and m.is_active and model_is_available(m)]
        if active:
            return active[0]
        fallback = [m for m in models if m.role == role and model_is_available(m)]
        if active or fallback:
            if fallback:
                log.warning("no usable active %s model; falling back to %s", role, fallback[0].name)
            return fallback[0] if fallback else None
        return None

    def spec(m: ModelVersion) -> ModelSpec:
        return ModelSpec(m.name, resolve_weights(m.weights), m.class_map or {}, m.role)

    if profile == "specialized":
        p, v = pick("person"), pick("vehicle")
        if p and v:
            return profile, [spec(p), spec(v)]
        log.warning("specialized profile incomplete (person=%s, vehicle=%s); using shared", p, v)
    s = pick("shared")
    if s is None:
        raise RuntimeError("No usable detector model is registered")
    return "shared", [spec(s)]


def activate(db: Session, model: ModelVersion) -> None:
    for m in db.scalars(select(ModelVersion).where(ModelVersion.role == model.role)):
        m.is_active = m.id == model.id
    db.commit()
