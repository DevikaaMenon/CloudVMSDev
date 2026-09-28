"""Audit logging and small DB-backed system settings."""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from ..core.logging import request_id_var
from ..models import AuditLog, Setting, User

log = logging.getLogger("vms.audit")


def audit(db: Session, user: User | None, action: str, target_type: str = "", target_id: Any = "",
          details: dict | None = None, ip: str = "", commit: bool = True) -> None:
    entry = AuditLog(user_id=user.id if user else None, username=user.username if user else "",
                     action=action, target_type=target_type, target_id=str(target_id or ""),
                     details=details or {}, ip=ip, request_id=request_id_var.get())
    db.add(entry)
    if commit:
        db.commit()
    log.info("audit %s by %s on %s:%s", action, entry.username or "-", target_type, target_id)


DEFAULT_SETTINGS: dict[str, Any] = {
    "detector_profile": "shared",  # shared | specialized
    "models_version": 1,  # bumped when active models / profile change -> workers reload
}


def get_setting(db: Session, key: str) -> Any:
    row = db.get(Setting, key)
    return row.value if row else DEFAULT_SETTINGS.get(key)


def set_setting(db: Session, key: str, value: Any, commit: bool = True) -> None:
    row = db.get(Setting, key)
    if row:
        row.value = value
    else:
        db.add(Setting(key=key, value=value))
    if commit:
        db.commit()


def bump_models_version(db: Session) -> None:
    set_setting(db, "models_version", int(get_setting(db, "models_version") or 1) + 1)
