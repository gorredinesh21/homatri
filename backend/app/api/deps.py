"""Bearer JWT helpers shared by REST routers."""

from __future__ import annotations

from typing import Any

from fastapi import Depends, HTTPException, Request, status

from backend.app.core.security import bearer_payload, decode_access_token


def optional_payload(request: Request) -> dict[str, Any] | None:
    header = request.headers.get("authorization") or ""
    if not header.lower().startswith("bearer "):
        return None
    return decode_access_token(header.split(" ", 1)[1].strip())


def require_user(request: Request) -> dict[str, Any]:
    return bearer_payload(request)


def require_phone(payload: dict[str, Any] = Depends(require_user)) -> str:
    phone = payload.get("sub")
    if not phone:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token is missing a user id")
    return str(phone)


def require_role(*roles: str):
    async def _inner(payload: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
        role = payload.get("role")
        if role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"This action requires one of: {', '.join(roles)}",
            )
        return payload

    return _inner
