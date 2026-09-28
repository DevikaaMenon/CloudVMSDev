"""Administration (users, roles, audit log) and system health."""
from __future__ import annotations

import platform
import shutil
from typing import Optional

import psutil
from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from ..core.config import get_settings
from ..core.errors import bad_request, not_found
from ..core.security import hash_password, password_problems
from ..db import get_db
from ..deps import client_ip, require
from ..models import AuditLog, Camera, Role, User
from ..schemas import AuditOut, Page, RoleOut, UserCreate, UserOut, UserUpdate
from ..services.audit import audit, get_setting
from .auth import create_user

router = APIRouter(tags=["admin"])
psutil.cpu_percent(None)  # prime the counter so the first reading is meaningful


@router.get("/users", response_model=list[UserOut])
def list_users(user: User = Depends(require("users:manage")), db: Session = Depends(get_db)):
    return [UserOut.of(u) for u in db.scalars(select(User).order_by(User.id))]


@router.post("/users", response_model=UserOut, status_code=201)
def add_user(body: UserCreate, request: Request, admin: User = Depends(require("users:manage")),
             db: Session = Depends(get_db)):
    return create_user(db, body, admin, client_ip(request))


@router.get("/roles", response_model=list[RoleOut])
def list_roles(user: User = Depends(require("users:manage")), db: Session = Depends(get_db)):
    return db.scalars(select(Role).order_by(Role.id)).all()


def _admins_left(db: Session, excluding: int) -> int:
    admin_role = db.scalar(select(Role).where(Role.name == "admin"))
    return sum(1 for u in db.scalars(select(User).where(User.is_active))
               if u.id != excluding and admin_role in u.roles)


@router.patch("/users/{user_id}", response_model=UserOut)
def update_user(user_id: int, body: UserUpdate, request: Request, admin: User = Depends(require("users:manage")),
                db: Session = Depends(get_db)):
    u = db.get(User, user_id)
    if u is None:
        raise not_found("User")
    changes = body.model_dump(exclude_unset=True)
    if body.roles is not None:
        roles = list(db.scalars(select(Role).where(Role.name.in_(body.roles))))
        if len(roles) != len(set(body.roles)):
            raise bad_request("Unknown role")
        if "admin" not in body.roles and any(r.name == "admin" for r in u.roles) and _admins_left(db, u.id) == 0:
            raise bad_request("At least one active administrator must remain")
        u.roles = roles
        u.token_version += 1  # permissions changed -> force re-login
    if body.is_active is False and u.id == admin.id:
        raise bad_request("You cannot deactivate your own account")
    if body.is_active is not None:
        u.is_active = body.is_active
        u.token_version += 1
    if body.full_name is not None:
        u.full_name = body.full_name
    if body.scope_all_cameras:
        u.camera_scope = None
    elif body.camera_scope is not None:
        valid = set(db.scalars(select(Camera.id)))
        u.camera_scope = [c for c in body.camera_scope if c in valid]
    if body.password:
        problems = password_problems(body.password)
        if problems:
            raise bad_request("Password needs " + ", ".join(problems))
        u.password_hash = hash_password(body.password)
        u.must_change_password = True
        u.token_version += 1
        changes["password"] = "***"
    audit(db, admin, "user.update", "user", u.id, changes, client_ip(request))
    db.refresh(u)
    return UserOut.of(u)


@router.patch("/users/{user_id}/roles", response_model=UserOut)
def set_roles(user_id: int, roles: list[str], request: Request, admin: User = Depends(require("users:manage")),
              db: Session = Depends(get_db)):
    return update_user(user_id, UserUpdate(roles=roles), request, admin, db)


@router.get("/audit-logs", response_model=Page[AuditOut])
def audit_logs(action: Optional[str] = None, username: Optional[str] = None, page: int = Query(1, ge=1),
               page_size: int = Query(50, ge=1, le=500), user: User = Depends(require("audit:view")),
               db: Session = Depends(get_db)):
    q = select(AuditLog)
    if action:
        q = q.where(AuditLog.action.like(f"{action}%"))
    if username:
        q = q.where(AuditLog.username == username)
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    rows = db.scalars(q.order_by(desc(AuditLog.ts)).offset((page - 1) * page_size).limit(page_size)).all()
    return Page(items=rows, total=total, page=page, page_size=page_size)


@router.get("/system/health")
def system_health(user: User = Depends(require("system:view")), db: Session = Depends(get_db)):
    from ..services.storage import get_storage
    from ..workers.supervisor import get_supervisor
    s = get_settings()
    sup = get_supervisor()
    vm = psutil.virtual_memory()
    disk = shutil.disk_usage(s.data_dir)
    gpu = None
    try:
        import torch
        if torch.cuda.is_available():
            gpu = {"name": torch.cuda.get_device_name(0),
                   "memory_allocated_mb": round(torch.cuda.memory_allocated() / 1e6, 1),
                   "memory_total_mb": round(torch.cuda.get_device_properties(0).total_memory / 1e6, 1)}
    except Exception:
        pass
    return {
        "api": "ok",
        "database": "ok" if db.execute(select(1)).scalar() == 1 else "error",
        "storage_backend": get_storage().backend,
        "embedded_workers": s.embedded_workers,
        "supervisor": sup.status() if sup else None,
        "detector_profile": get_setting(db, "detector_profile"),
        "host": {"platform": platform.platform(), "python": platform.python_version(),
                 "cpu_count": psutil.cpu_count(), "cpu_percent": psutil.cpu_percent(interval=None),
                 "memory_used_mb": round((vm.total - vm.available) / 1e6), "memory_total_mb": round(vm.total / 1e6),
                 "disk_free_gb": round(disk.free / 1e9, 1), "disk_total_gb": round(disk.total / 1e9, 1), "gpu": gpu},
    }
