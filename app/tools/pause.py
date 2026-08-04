"""The pause / await-reply primitive.

`send_and_await_reply` lets a tool send a message and pause the turn until the
user replies with a specific `await_type`. It works via a **check-first router**:
the router inspects the pending-await note BEFORE routing to the agent, so the
reply resumes the paused step instead of being re-processed by the LLM.

This is the lightweight (dev/harness) version over an in-memory store. The real
runtime does the same thing with LangGraph `interrupt()` + a Postgres
checkpointer — the `send_and_await_reply` interface stays identical, so tools
that use it won't change when we swap in LangGraph.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable

DEFAULT_TTL_MINS = 15


class Pause(Exception):
    """Raised by send_and_await_reply to unwind the current turn and wait for a reply."""

    def __init__(self, recipient: str, message: str, await_type: str):
        super().__init__(message)
        self.recipient = recipient
        self.message = message
        self.await_type = await_type


# In-memory pending-await store, keyed by phone (dev only; real = system_hitl_sessions + checkpoint).
_pending: dict[str, dict[str, Any]] = {}

# Resume handlers, registered by name. Signature: async (phone, reply, ctx) -> str
RESUME_HANDLERS: dict[str, Callable[..., Awaitable[str]]] = {}


def resume_handler(name: str):
    """Register a resume handler under `name` (so a pending note can point to it)."""

    def deco(fn: Callable[..., Awaitable[str]]):
        RESUME_HANDLERS[name] = fn
        return fn

    return deco


def send_and_await_reply(
    recipient: str,
    message: str,
    *,
    await_type: str,
    resume: str,
    ctx: dict[str, Any] | None = None,
    ttl_mins: int = DEFAULT_TTL_MINS,
) -> None:
    """Send `message` to `recipient`, record a pending-await note, and raise Pause.

    When a reply of `await_type` arrives, the router runs
    `RESUME_HANDLERS[resume](recipient, reply, ctx)`.
    """
    _pending[recipient] = {
        "await_type": await_type,
        "resume": resume,
        "ctx": ctx or {},
        "prompt": message,
        "expires_at": datetime.now() + timedelta(minutes=ttl_mins),
    }
    raise Pause(recipient, message, await_type)


def get_pending(phone: str) -> dict[str, Any] | None:
    return _pending.get(phone)


def clear_pending(phone: str) -> None:
    _pending.pop(phone, None)


def is_expired(note: dict[str, Any]) -> bool:
    return datetime.now() > note["expires_at"]
