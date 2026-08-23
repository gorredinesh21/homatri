"""JWT access tokens and hashed 30-day refresh sessions."""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

import jwt
from fastapi import HTTPException, Request, status

from backend.app.core.config import settings

ALGORITHM = "HS256"
REFRESH_COOKIE = "homatri_refresh"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def new_refresh_token() -> str:
    return secrets.token_urlsafe(48)


def create_access_token(*, user_id: str, role: str, session_id: UUID) -> str:
    now = utcnow()
    payload = {
        "sub": user_id,
        "role": role,
        "sid": str(session_id),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=settings.jwt_access_ttl_seconds)).timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=ALGORITHM)


def decode_access_token(token: str) -> dict[str, Any]:
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Access token expired") from exc
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid access token") from exc


def bearer_payload(request: Request) -> dict[str, Any]:
    header = request.headers.get("authorization") or ""
    if not header.lower().startswith("bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token")
    return decode_access_token(header.split(" ", 1)[1].strip())
