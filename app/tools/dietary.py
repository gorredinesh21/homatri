"""Flow 6 Part B — dietary negotiation (bounded 2-turn HITL across 3 agents).

Customer requests a custom note -> Master relays it to the Chef -> Chef accepts /
rejects / counters -> the outcome is pushed back to the customer (who answers a
counter). Max 2 rounds; unresolved -> keep the original order.

Harness note: the negotiation state lives in an in-memory store (real runtime =
system_hitl_sessions). Outcomes are pushed via the outbound queue, which the
widgets poll — so a chef's answer appears in the customer's chat without the
customer typing.
"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import tool
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ids import generate_id
from app.db.session import transaction
from app.executors.master import execute_outbound_whatsapp_enqueue
from app.models.chef import ChefProfile
from app.models.customer import CustomerOrder
from app.tools.delegate import delegate_write
from app.tools.pause import arm_await, clear_pending, resume_handler

MODIFIABLE_ORDER_STATUSES = ("CONFIRMED", "BATCHED", "COOKING")
MAX_TURNS = 2

# In-memory negotiation store, keyed by customer_phone (one open request per customer).
_negotiations: dict[str, dict[str, Any]] = {}


def _open_for_chef(chef_phone: str) -> dict | None:
    for n in _negotiations.values():
        if n["chef_phone"] == chef_phone and n["status"] == "WAITING_CHEF":
            return n
    return None


def clear_negotiation(phone: str) -> None:
    _negotiations.pop(phone, None)


# =============================================================================
# TOOL: relay_dietary_request  (Master · cross-domain)
# =============================================================================
class RelayDietaryRequestInput(BaseModel):
    order_id: str = Field(..., description="The order the request is about.")
    customer_phone: str = Field(..., description="The requesting customer.")
    chef_phone: str = Field(..., description="The kitchen to ask.")
    note: str = Field(..., description="The custom/dietary note, e.g. 'no garlic'.")
    turn: int = Field(default=1, description="Negotiation round (1 or 2).")


async def _relay_dietary_request(
    session: AsyncSession, *, order_id: str, customer_phone: str, chef_phone: str, note: str, turn: int = 1
) -> dict[str, Any]:
    """Notify the chef of a dietary request. Guards: turn>2 -> KEPT_ORIGINAL; chef missing."""
    if turn > MAX_TURNS:
        return {"status": "KEPT_ORIGINAL",
                "message": f"No agreement after {MAX_TURNS} rounds — keeping the original order."}
    chef = await session.get(ChefProfile, chef_phone)
    if chef is None:
        return {"status": "NOT_FOUND", "message": f"Kitchen {chef_phone} not found."}
    await execute_outbound_whatsapp_enqueue(
        session, recipient_phone=chef_phone, recipient_role="CHEF", related_order_id=order_id,
        message_text=(f"🙋 Dietary request on order {order_id}: \"{note}\".\n"
                      f"Reply: accept · reject · counter <your offer>"),
    )
    return {"status": "SENT_TO_CHEF", "message": f"Relayed to {chef.kitchen_name}."}


@tool("relay_dietary_request", args_schema=RelayDietaryRequestInput)
async def relay_dietary_request(order_id: str, customer_phone: str, chef_phone: str, note: str, turn: int = 1) -> str:
    """Master: route a customer's dietary note to the chef (enforces the 2-round cap)."""
    async with transaction() as session:
        res = await _relay_dietary_request(
            session, order_id=order_id, customer_phone=customer_phone, chef_phone=chef_phone, note=note, turn=turn)
        return res["message"]


# =============================================================================
# TOOL: request_dietary_change  (Customer · cross-domain)
# =============================================================================
class RequestDietaryChangeInput(BaseModel):
    customer_phone: str = Field(..., description="Normalized 10-digit customer phone.")
    note: str = Field(..., description="The custom/dietary request, e.g. 'no garlic', 'less spicy'.")


async def _request_dietary_change(
    session: AsyncSession, *, customer_phone: str, note: str
) -> dict[str, Any]:
    """Kick off a dietary negotiation on the customer's active order.

    Guards:
      - a request is already pending          -> ALREADY_PENDING
      - no changeable order (must be CONFIRMED/BATCHED/COOKING) -> NOT_MODIFIABLE
    """
    existing = _negotiations.get(customer_phone)
    if existing is not None and existing["status"] != "RESOLVED":
        return {"status": "ALREADY_PENDING", "message": "You already have a request pending with the kitchen."}

    order = (
        await session.execute(
            select(CustomerOrder).where(
                CustomerOrder.customer_phone == customer_phone,
                CustomerOrder.status.in_(MODIFIABLE_ORDER_STATUSES),
            ).order_by(CustomerOrder.created_at.desc())
        )
    ).scalars().first()
    if order is None:
        return {"status": "NOT_MODIFIABLE",
                "message": "You don't have an order that can be changed right now (it must be confirmed and not yet packed)."}

    _negotiations[customer_phone] = {
        "request_id": generate_id("diet"), "order_id": order.order_id, "customer_phone": customer_phone,
        "chef_phone": order.chef_phone, "kitchen_name": order.kitchen_name, "note": note,
        "counter": None, "turn": 1, "status": "WAITING_CHEF",
    }
    relay = await _relay_dietary_request(
        session, order_id=order.order_id, customer_phone=customer_phone,
        chef_phone=order.chef_phone, note=note, turn=1)
    if relay["status"] != "SENT_TO_CHEF":
        _negotiations.pop(customer_phone, None)
        return relay
    return {"status": "AWAITING_CHEF",
            "message": f"Got it — I've asked {order.kitchen_name} about \"{note}\". Hang tight, I'll let you know their answer! ⏳"}


@tool("request_dietary_change", args_schema=RequestDietaryChangeInput)
async def request_dietary_change(customer_phone: str, note: str) -> str:
    """Ask the kitchen for a custom/dietary change on the customer's order; wait for their answer."""
    async with transaction() as session:
        res = await _request_dietary_change(session, customer_phone=customer_phone, note=note)
        return res["message"]


# =============================================================================
# TOOL: respond_to_dietary_request  (Chef · cross-domain)
# =============================================================================
class RespondToDietaryRequestInput(BaseModel):
    chef_phone: str = Field(..., description="Normalized 10-digit chef phone.")
    decision: str = Field(..., description="'accept', 'reject', or 'counter'.")
    counter_note: str | None = Field(default=None, description="Required for a counter, e.g. 'I can do less garlic'.")


async def _respond_to_dietary_request(
    session: AsyncSession, *, chef_phone: str, decision: str, counter_note: str | None = None
) -> dict[str, Any]:
    """Chef's decision; resolves or bounces the negotiation to Round 2.

    Guards: no open request -> NO_REQUEST; COUNTER without a note -> NEED_COUNTER.
    """
    n = _open_for_chef(chef_phone)
    if n is None:
        return {"status": "NO_REQUEST", "message": "You have no pending dietary requests."}
    d = decision.strip().upper()
    cust, order_id = n["customer_phone"], n["order_id"]

    if d in ("ACCEPT", "ACCEPTED"):
        await delegate_write(session, requesting_role="CHEF", capability="ORDER_NOTE",
                             order_id=order_id, special_instructions=n["note"])
        n["status"] = "RESOLVED"
        clear_pending(cust)
        await execute_outbound_whatsapp_enqueue(
            session, recipient_phone=cust, recipient_role="CUSTOMER", related_order_id=order_id,
            message_text=f"✅ Good news! {n['kitchen_name']} agreed to: \"{n['note']}\". It's noted on your order.")
        return {"status": "RESOLVED", "message": f"Accepted — customer notified, note saved on order {order_id}."}

    if d in ("REJECT", "REJECTED"):
        n["status"] = "RESOLVED"
        clear_pending(cust)
        await execute_outbound_whatsapp_enqueue(
            session, recipient_phone=cust, recipient_role="CUSTOMER", related_order_id=order_id,
            message_text=f"❌ {n['kitchen_name']} can't do \"{n['note']}\" — keeping your original order.")
        return {"status": "RESOLVED", "message": "Rejected — customer notified, original kept."}

    if d == "COUNTER":
        if not (counter_note or "").strip():
            return {"status": "NEED_COUNTER", "message": "Add your counter offer, e.g. counter_note='I can do less garlic'."}
        n["counter"] = counter_note.strip()
        n["turn"] = 2
        n["status"] = "WAITING_CUSTOMER"
        # arm the customer to reply yes/no (text-resumable await -> resolve_dietary_counter)
        arm_await(cust, await_type="CUSTOMER_DIETARY_DECISION", resume="resolve_dietary_counter",
                  ctx={"customer_phone": cust})
        await execute_outbound_whatsapp_enqueue(
            session, recipient_phone=cust, recipient_role="CUSTOMER", related_order_id=order_id,
            message_text=(f"🔁 {n['kitchen_name']} can't do \"{n['note']}\" exactly, but offers: "
                          f"\"{n['counter']}\". Reply 'accept' to take it, or 'no' to keep your original."))
        return {"status": "COUNTER_SENT", "message": "Counter offer sent to the customer."}

    return {"status": "INVALID", "message": "Decision must be accept, reject, or counter."}


@tool("respond_to_dietary_request", args_schema=RespondToDietaryRequestInput)
async def respond_to_dietary_request(chef_phone: str, decision: str, counter_note: str | None = None) -> str:
    """Chef: accept / reject / counter a pending dietary request (resumes the waiting customer)."""
    async with transaction() as session:
        res = await _respond_to_dietary_request(
            session, chef_phone=chef_phone, decision=decision, counter_note=counter_note)
        return res["message"]


# =============================================================================
# Resume handler: the customer answers a counter-offer (Round 2, text-resumable)
# =============================================================================
async def _apply_counter_accept(session: AsyncSession, *, phone: str) -> None:
    """Save an accepted counter-offer note onto the order (via delegate_write)."""
    n = _negotiations[phone]
    await delegate_write(session, requesting_role="MASTER", capability="ORDER_NOTE",
                         order_id=n["order_id"], special_instructions=n["counter"])


@resume_handler("resolve_dietary_counter")
async def resolve_dietary_counter(phone: str, reply: dict[str, Any], ctx: dict[str, Any]) -> str:
    n = _negotiations.get(phone)
    if n is None or n["status"] != "WAITING_CUSTOMER":
        return "That request already wrapped up."
    text = (reply.get("text") or "").strip().lower()
    accepted = any(w in text for w in ("accept", "yes", "ok", "sure", "take it", "deal"))
    kitchen, counter = n["kitchen_name"], n["counter"]
    if accepted:
        async with transaction() as session:
            await _apply_counter_accept(session, phone=phone)
        n["status"] = "RESOLVED"
        return f"✅ Done — {kitchen} will do \"{counter}\". Noted on your order!"
    n["status"] = "RESOLVED"
    return "No worries — keeping your original order as-is. 🙂"
