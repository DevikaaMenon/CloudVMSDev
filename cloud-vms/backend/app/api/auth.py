"""Authentication: login / logout / me / register / change password."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.config import get_settings
from ..core.errors import AppError, RateLimiter, bad_request
from ..core.security import create_access_token, hash_password, password_problems, verify_password
from ..core.timeutil import utcnow
from ..db import get_db
from ..deps import client_ip, get_current_user, require
from ..models import Role, User
from ..schemas import LoginIn, PasswordChange, TokenOut, UserCreate, UserOut
from ..services.audit import audit

router = APIRouter(prefix="/auth", tags=["auth"])
_login_limiter = RateLimiter(get_settings().login_rate_limit_per_minute, 60)


@router.post("/login", response_model=TokenOut)
def login(body: LoginIn, request: Request, db: Session = Depends(get_db)):
    ip = client_ip(request)
    if not _login_limiter.check(f"{ip}:{body.username.lower()}"):
        raise AppError(429, "rate_limited", "Too many login attempts, wait a minute and try again")
    user = db.scalar(select(User).where(User.username == body.username))
    if user is None or not user.is_active or not verify_password(body.password, user.password_hash):
        audit(db, None, "auth.login_failed", "user", "", {"username": body.username}, ip)
        raise AppError(401, "invalid_credentials", "Wrong username or password")
    user.last_login_at = utcnow()
    audit(db, user, "auth.login", "user", user.id, ip=ip)
    return TokenOut(access_token=create_access_token(user.id, user.token_version),
                    expires_in=get_settings().jwt_ttl_minutes * 60, user=UserOut.of(user))


@router.post("/logout")
def logout(request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    user.token_version += 1  # invalidates every token issued so far
    audit(db, user, "auth.logout", "user", user.id, ip=client_ip(request))
    return {"ok": True}


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return UserOut.of(user)


@router.post("/change-password")
def change_password(body: PasswordChange, request: Request, user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    if not verify_password(body.current_password, user.password_hash):
        raise bad_request("Current password is incorrect")
    problems = password_problems(body.new_password)
    if problems:
        raise bad_request("Password needs " + ", ".join(problems))
    user.password_hash = hash_password(body.new_password)
    user.must_change_password = False
    user.token_version += 1
    audit(db, user, "auth.password_changed", "user", user.id, ip=client_ip(request))
    return {"ok": True, "access_token": create_access_token(user.id, user.token_version)}


@router.post("/register", response_model=UserOut, status_code=201)
def register(body: UserCreate, request: Request, admin: User = Depends(require("users:manage")),
             db: Session = Depends(get_db)):
    """Accounts are created by an administrator (no public self-registration for a security system)."""
    return create_user(db, body, admin, client_ip(request))


def create_user(db: Session, body: UserCreate, actor: User, ip: str) -> UserOut:
    if db.scalar(select(User).where(User.username == body.username)):
        raise bad_request("That username is already taken")
    problems = password_problems(body.password)
    if problems:
        raise bad_request("Password needs " + ", ".join(problems))
    roles = list(db.scalars(select(Role).where(Role.name.in_(body.roles))))
    if len(roles) != len(set(body.roles)):
        raise bad_request("Unknown role in: " + ", ".join(body.roles))
    u = User(username=body.username, full_name=body.full_name, password_hash=hash_password(body.password),
             camera_scope=body.camera_scope, roles=roles)
    db.add(u)
    db.flush()
    audit(db, actor, "user.create", "user", u.id, {"username": u.username, "roles": body.roles}, ip)
    db.refresh(u)
    return UserOut.of(u)
