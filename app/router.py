"""The check-first router.

Every inbound message hits this first. Before routing to the agent, it asks:
"is this conversation paused, waiting for a reply?" — and if so, routes the reply
to the paused step (resume handler) instead of the LLM. That's what makes a reply
come *back to the paused point* rather than getting re-processed by the agent.

`run_agent(phone, text) -> {reply, await_location}` is injected, so this router is
agnostic to which agent runtime runs it (dev harness Bedrock loop now; LangGraph
later).
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from app.tools.pause import RESUME_HANDLERS, clear_pending, get_pending, is_expired

RunAgent = Callable[[str, str], Awaitable[dict[str, Any]]]

# Awaits resumed by an EXTERNAL callback (e.g. the payment webhook), not by the
# customer's next WhatsApp message. These must survive off-topic chatter — a
# customer asking "did it go through?" must NOT cancel a pending payment.
OUT_OF_BAND_AWAITS = {"PAYMENT_CONFIRM"}


async def route(msg: dict[str, Any], run_agent: RunAgent) -> dict[str, Any]:
    """Route one normalized message. Returns {reply, await_location}."""
    phone = msg["phone"]
    note = get_pending(phone)

    if note:
        # 1) timeout -> pending-state rollback
        if is_expired(note):
            clear_pending(phone)
            return {"reply": "That timed out. Say 'hi' to start again.", "await_location": False, "expired": True}

        # 2) the awaited reply arrived -> resume the paused step (NOT the agent)
        if msg["type"] == "location" and note["await_type"] == "LOCATION_PIN":
            reply = await RESUME_HANDLERS[note["resume"]](phone, msg["location"], note["ctx"])
            clear_pending(phone)
            return {"reply": reply, "await_location": False}

        # 3) something else arrived. For user-resumable awaits, "new message wins":
        #    drop the pending op and treat as fresh. For out-of-band awaits (payment),
        #    KEEP the pending note — only the external callback (/pay) or timeout ends it.
        if note["await_type"] not in OUT_OF_BAND_AWAITS:
            clear_pending(phone)

    # normal path: hand to the agent
    text = msg.get("text") or f"(the customer shared their location: {msg.get('location')})"
    return await run_agent(phone, text)
