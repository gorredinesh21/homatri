"""Admin & realtime endpoints: world state, reset, health, LLM preflight, SSE."""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter
from sse_starlette.sse import EventSourceResponse

from app.core.config import settings
from app.db.session import SessionLocal
from app.seed import reset_and_seed
from app.services.events import bus
from app.services.llm import llm
from app.services.state_snapshot import build_state

router = APIRouter(prefix="/api", tags=["admin"])


@router.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "whatsapp_provider": settings.whatsapp_provider,
        "payment_provider": settings.payment_provider,
        "llm_enabled": settings.llm_enabled,
    }


@router.get("/preflight")
async def preflight() -> dict:
    return await llm.preflight()


@router.get("/state")
async def state() -> dict:
    async with SessionLocal() as session:
        return await build_state(session)


@router.post("/reset")
async def reset() -> dict:
    async with SessionLocal() as session:
        await reset_and_seed(session)
        st = await build_state(session)
    await bus.publish({"kind": "state", "state": st})
    return {"status": "reset", "state": st}


@router.get("/stream")
async def stream():
    """Server-Sent Events: pushes wa_message / wa_status / state events."""

    async def event_gen():
        # send a hello + current state immediately
        async with SessionLocal() as session:
            st = await build_state(session)
        yield {"event": "message", "data": json.dumps({"kind": "state", "state": st})}
        async for event in bus.subscribe():
            yield {"event": "message", "data": json.dumps(event)}

    return EventSourceResponse(event_gen())
