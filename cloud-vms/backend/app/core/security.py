"""Password hashing, JWT tokens, signed media URLs and secret encryption."""
from __future__ import annotations

import base64
import hashlib
import hmac
import time
from typing import Any

import bcrypt
import jwt
from cryptography.fernet import Fernet, InvalidToken

from .config import get_settings

ALGO = "HS256"


# ---------------------------------------------------------------- passwords
def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8")[:72], bcrypt.gensalt(rounds=12)).decode()


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8")[:72], hashed.encode())
    except ValueError:
        return False


def password_problems(password: str) -> list[str]:
    problems = []
    if len(password) < 8:
        problems.append("at least 8 characters")
    if not any(c.isdigit() for c in password):
        problems.append("a digit")
    if not any(c.isalpha() for c in password):
        problems.append("a letter")
    return problems


# ---------------------------------------------------------------- JWT
def create_access_token(user_id: int, token_version: int) -> str:
    s = get_settings()
    now = int(time.time())
    payload = {"sub": str(user_id), "tv": token_version, "typ": "access",
               "iat": now, "exp": now + s.jwt_ttl_minutes * 60}
    return jwt.encode(payload, s.resolved_secret(), algorithm=ALGO)


def create_stream_token(user_id: int, token_version: int, scope: str) -> str:
    """Short-lived token for URLs that cannot carry headers (<img>, EventSource)."""
    s = get_settings()
    now = int(time.time())
    payload = {"sub": str(user_id), "tv": token_version, "typ": "stream", "scp": scope,
               "iat": now, "exp": now + s.stream_token_ttl_seconds}
    return jwt.encode(payload, s.resolved_secret(), algorithm=ALGO)


def decode_token(token: str, expected_type: str) -> dict[str, Any]:
    data = jwt.decode(token, get_settings().resolved_secret(), algorithms=[ALGO])
    if data.get("typ") != expected_type:
        raise jwt.InvalidTokenError("wrong token type")
    return data


# ---------------------------------------------------------------- signed media URLs
def sign_media(key: str, expires_at: int) -> str:
    msg = f"{key}|{expires_at}".encode()
    return hmac.new(get_settings().resolved_secret().encode(), msg, hashlib.sha256).hexdigest()


def verify_media_signature(key: str, expires_at: int, signature: str) -> bool:
    if expires_at < int(time.time()):
        return False
    return hmac.compare_digest(sign_media(key, expires_at), signature)


# ---------------------------------------------------------------- secret encryption
def _fernet() -> Fernet:
    digest = hashlib.sha256(("vms-credentials:" + get_settings().resolved_secret()).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt_secret(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken:
        return ""
