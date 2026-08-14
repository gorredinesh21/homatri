"""Post-payment top-up — adding extra items to an already-paid order (Customer + Chef + delta payment).

The customer asks to add dishes to an order that's already been paid for
(CONFIRMED/BATCHED/COOKING). It's the dietary negotiation shape (Customer -> Master
-> Chef, accept/reject/counter) with a **payment leg** on the "yes" path: the chef
must approve, the customer must pay the delta, and only *then* are the items appended
to the order.

Flow:
  1. request_order_topup (Customer): last order must exist AND be paid; resolves the
     extra dishes + delta, opens the negotiation, relays to Master.
  2. relay_topup_request (Master): notifies the chef.
  3. respond_to_topup_request (Chef): accept / reject / counter.
       - accept  -> mint the delta link, push it to the customer, arm their payment.
       - counter -> chef types a note (forwarded verbatim) + the qty they CAN do;
                    push to the customer, arm a yes/no text await.
       - reject  -> tell the customer, discard.
  4. resolve_topup_counter (resume): customer's yes/no to a counter. yes -> mint the
     link for the chef's adjusted qty + arm payment; no -> discard.
  5. confirm_topup_payment (resume, fired by /pay): mark the TOPUP payment PAID (no
     order-status cascade), append the items, notify the chef + customer.

Harness note: negotiation state is in-memory (real runtime = system_hitl_sessions).
The delta bill reuses the existing PAYMENT_CONFIRM await, so the same /pay callback
and 💳 Pay button drive it — only the resume handler differs.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from langchain_core.tools import tool
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.ids import generate_id
from backend.app.db.session import transaction
from backend.app.executors.customer import execute_append_items_to_order, execute_payment_status_update
from backend.app.executors.master import execute_outbound_whatsapp_enqueue
from backend.app.models.chef import ChefProfile
from backend.app.models.customer import CustomerOrder
from backend.app.tools.customer_tools import _norm_items, _resolve_dish
from backend.app.tools.master_tools import _mint_topup_payment_link
from backend.app.tools.pause import arm_await, clear_pending, resume_handler

# An order can only be topped up once it's paid and before it's packed.
TOPUPPABLE_STATUSES = ("CONFIRMED", "BATCHED", "COOKING")
UNPAID_STATUSES = ("DRAFT_CART", "PENDING_PAYMENT")

# In-memory top-up store, keyed by customer_phone (one open top-up per customer).
_topups: dict[str, dict[str, Any]] = {}


def _open_topup_for_chef(chef_phone: str) -> dict | None:
    for n in _topups.values():
        if n["chef_phone"] == chef_phone and n["status"] == "WAITING_CHEF":
            return n
    return None


def clear_topup(phone: str) -> None:
    _topups.pop(phone, None)


def _summarize(items: list[dict]) -> str:
    return ", ".join(f"{i['quantity']}× {i['dish_name']}" for i in items)


async def _resolve_topup_items(
    session: AsyncSession, *, chef_phone: str, meal_window: str, items: list[dict]
) -> tuple[list[dict], Decimal, str | None]:
    """Resolve [{dish_name, quantity}] against a kitchen's menu. Returns (resolved, delta, error).

    resolved = [{menu_item_id, dish_name, quantity, unit_price}]; delta = sum(qty*price).
    """
    if not items:
        return [], Decimal("0.00"), "No dishes given to add."
    resolved: list[dict] = []
    total = Decimal("0.00")
    for it in items:
        ref = it.get("dish_name", "")
        qty = int(it.get("quantity", 1))
        if qty < 1:
            return [], Decimal("0.00"), f"Quantity for '{ref}' must be at least 1."
        dish, derr = await _resolve_dish(session, chef_phone=chef_phone, ref=ref, meal_type=meal_window)
        if derr == "AMBIGUOUS":
            return [], Decimal("0.00"), f"Several dishes match '{ref}'. Ask which one."
        if dish is None:
            return [], Decimal("0.00"), f"'{ref}' isn't on the kitchen's menu right now."
        resolved.append({
            "menu_item_id": dish.menu_item_id, "dish_name": dish.dish_name,
            "quantity": qty, "unit_price": dish.unit_price,
        })
        total += dish.unit_price * qty
    return resolved, total, None


# =============================================================================
# TOOL: relay_topup_request  (Master · cross-domain)
# =============================================================================
class RelayTopupRequestInput(BaseModel):
    order_id: str = Field(..., description="The order the add-on is for.")
    customer_phone: str = Field(..., description="The requesting customer.")
    chef_phone: str = Field(..., description="The kitchen to ask.")
    summary: str = Field(..., description="The extra items, e.g. '2× Paneer'.")
    amount: float = Field(..., description="The delta charge in rupees.")


async def _relay_topup_request(
    session: AsyncSession, *, order_id: str, customer_phone: str, chef_phone: str, summary: str, amount: float
) -> dict[str, Any]:
    """Notify the chef of an add-on request. Guard: chef missing -> NOT_FOUND."""
    chef = await session.get(ChefProfile, chef_phone)
    if chef is None:
        return {"status": "NOT_FOUND", "message": f"Kitchen {chef_phone} not found."}
    await execute_outbound_whatsapp_enqueue(
        session, recipient_phone=chef_phone, recipient_role="CHEF", related_order_id=order_id,
        message_text=(f"➕ Add-on request on order {order_id}: {summary} (₹{amount:.0f}).\n"
                      f"Reply: accept · reject · counter <note> (if you can only do fewer, "
                      f"say counter with the qty you can add)."),
    )
    return {"status": "SENT_TO_CHEF", "message": f"Relayed the add-on to {chef.kitchen_name}."}


@tool("relay_topup_request", args_schema=RelayTopupRequestInput)
async def relay_topup_request(order_id: str, customer_phone: str, chef_phone: str, summary: str, amount: float) -> str:
    """Master: route a customer's add-item request to the chef for approval."""
    async with transaction() as session:
        res = await _relay_topup_request(
            session, order_id=order_id, customer_phone=customer_phone,
            chef_phone=chef_phone, summary=summary, amount=amount)
        return res["message"]


# =============================================================================
# TOOL: request_order_topup  (Customer · cross-domain)
# =============================================================================
class RequestOrderTopupInput(BaseModel):
    customer_phone: str = Field(..., description="Normalized 10-digit customer phone.")
    items: list[dict] = Field(..., description="Extra dishes to add, each {dish_name, quantity}.")


async def _request_order_topup(
    session: AsyncSession, *, customer_phone: str, items: list[dict]
) -> dict[str, Any]:
    """Start a top-up (add extra items) on the customer's already-paid order.

    Guards:
      - a top-up is already pending                 -> ALREADY_PENDING
      - no order at all                             -> NO_ORDER (place an order first)
      - last order still unpaid (draft/pending)     -> UNPAID (pay for it first)
      - last order past cooking (packed/delivered…) -> TOO_LATE
      - unknown / ambiguous dish                    -> INVALID_ITEM
    """
    existing = _topups.get(customer_phone)
    if existing is not None and existing["status"] != "RESOLVED":
        return {"status": "ALREADY_PENDING", "message": "You already have an add-on request pending with the kitchen."}

    order = (
        await session.execute(
            select(CustomerOrder).where(CustomerOrder.customer_phone == customer_phone)
            .order_by(CustomerOrder.created_at.desc())
        )
    ).scalars().first()
    if order is None:
        return {"status": "NO_ORDER", "message": "You don't have an order yet — place one first, then you can add to it."}
    if order.status in UNPAID_STATUSES:
        return {"status": "UNPAID",
                "message": f"Your order {order.order_id} isn't paid yet — pay for it first (request_payment), then add extras."}
    if order.status not in TOPUPPABLE_STATUSES:
        return {"status": "TOO_LATE",
                "message": f"Order {order.order_id} is {order.status.lower()} — too late to add items to it."}

    resolved, delta, err = await _resolve_topup_items(
        session, chef_phone=order.chef_phone, meal_window=order.meal_window, items=_norm_items(items))
    if err is not None:
        return {"status": "INVALID_ITEM", "message": err}

    _topups[customer_phone] = {
        "request_id": generate_id("top"), "order_id": order.order_id, "customer_phone": customer_phone,
        "chef_phone": order.chef_phone, "kitchen_name": order.kitchen_name,
        "items": resolved, "amount": float(delta),
        "counter_items": None, "counter_amount": None,
        "final_items": None, "payment_id": None, "status": "WAITING_CHEF",
    }
    relay = await _relay_topup_request(
        session, order_id=order.order_id, customer_phone=customer_phone, chef_phone=order.chef_phone,
        summary=_summarize(resolved), amount=float(delta))
    if relay["status"] != "SENT_TO_CHEF":
        _topups.pop(customer_phone, None)
        return relay
    return {"status": "AWAITING_CHEF",
            "message": (f"Got it — I've asked {order.kitchen_name} if they can add {_summarize(resolved)} "
                        f"(₹{float(delta):.0f}). I'll let you know their answer! ⏳")}


@tool("request_order_topup", args_schema=RequestOrderTopupInput)
async def request_order_topup(customer_phone: str, items: list) -> str:
    """Ask the kitchen to add extra dishes to the customer's already-paid order; wait for the chef's answer."""
    async with transaction() as session:
        res = await _request_order_topup(session, customer_phone=customer_phone, items=items)
        return res["message"]


# =============================================================================
# TOOL: respond_to_topup_request  (Chef · cross-domain)
# =============================================================================
class RespondToTopupRequestInput(BaseModel):
    chef_phone: str = Field(..., description="Normalized 10-digit chef phone.")
    decision: str = Field(..., description="'accept', 'reject', or 'counter'.")
    counter_note: str | None = Field(default=None, description="For a counter: your message to the customer (e.g. 'only 1 paneer left').")
    counter_items: list[dict] | None = Field(default=None, description="For a counter: the dishes+qty you CAN add, each {dish_name, quantity}.")


async def _mint_and_arm_payment(
    session: AsyncSession, *, n: dict, items: list[dict], amount: float
) -> dict[str, Any]:
    """Mint the delta link, arm the customer's payment await, and stash the agreed items."""
    mint = await _mint_topup_payment_link(
        session, order_id=n["order_id"], amount=Decimal(str(amount)),
        description=f"Homaatri order {n['order_id']} — extra {_summarize(items)}")
    if mint["status"] != "MINTED":
        return mint
    n["final_items"] = items
    n["payment_id"] = mint["payment_id"]
    n["status"] = "AWAITING_PAYMENT"
    arm_await(n["customer_phone"], await_type="PAYMENT_CONFIRM", resume="confirm_topup_payment",
              ctx={"customer_phone": n["customer_phone"], "payment_id": mint["payment_id"]})
    return mint


async def _respond_to_topup_request(
    session: AsyncSession, *, chef_phone: str, decision: str,
    counter_note: str | None = None, counter_items: list[dict] | None = None,
) -> dict[str, Any]:
    """Chef's decision on a top-up. Guards: no open request -> NO_REQUEST; bad counter -> NEED_COUNTER."""
    n = _open_topup_for_chef(chef_phone)
    if n is None:
        return {"status": "NO_REQUEST", "message": "You have no pending add-on requests."}
    d = decision.strip().upper()
    cust, order_id = n["customer_phone"], n["order_id"]

    if d in ("ACCEPT", "ACCEPTED", "YES", "OK"):
        summary = _summarize(n["items"])
        mint = await _mint_and_arm_payment(session, n=n, items=n["items"], amount=n["amount"])
        if mint["status"] != "MINTED":
            return {"status": mint["status"], "message": mint.get("message", "Couldn't start the top-up payment.")}
        await execute_outbound_whatsapp_enqueue(
            session, recipient_phone=cust, recipient_role="CUSTOMER", related_order_id=order_id,
            message_text=(f"✅ {n['kitchen_name']} can add {summary}! Pay ₹{n['amount']:.0f} to confirm:\n"
                          f"{mint['link']}\n\nOnce you pay, I'll add them to your order."))
        return {"status": "ACCEPTED", "message": f"Accepted — sent the customer a ₹{n['amount']:.0f} top-up link for {summary}."}

    if d in ("REJECT", "REJECTED", "NO", "DENY"):
        n["status"] = "RESOLVED"
        clear_pending(cust)
        clear_topup(cust)
        await execute_outbound_whatsapp_enqueue(
            session, recipient_phone=cust, recipient_role="CUSTOMER", related_order_id=order_id,
            message_text=f"❌ {n['kitchen_name']} can't add {_summarize(n['items'])} right now — your order is unchanged.")
        return {"status": "REJECTED", "message": "Rejected — customer notified, order unchanged."}

    if d == "COUNTER":
        if not (counter_note or "").strip():
            return {"status": "NEED_COUNTER", "message": "Add counter_note — your message to the customer (e.g. 'only 1 paneer left')."}
        if not counter_items:
            return {"status": "NEED_COUNTER", "message": "Add counter_items — the dishes + qty you CAN add, e.g. [{'dish_name':'Paneer','quantity':1}]."}
        order = await session.get(CustomerOrder, order_id)
        resolved, delta, err = await _resolve_topup_items(
            session, chef_phone=order.chef_phone, meal_window=order.meal_window, items=_norm_items(counter_items))
        if err is not None:
            return {"status": "NEED_COUNTER", "message": f"Couldn't read your counter items: {err}"}
        n["counter_items"] = resolved
        n["counter_amount"] = float(delta)
        n["status"] = "WAITING_CUSTOMER"
        # arm the customer to reply yes/no (text-resumable await -> resolve_topup_counter)
        arm_await(cust, await_type="CUSTOMER_TOPUP_DECISION", resume="resolve_topup_counter",
                  ctx={"customer_phone": cust})
        await execute_outbound_whatsapp_enqueue(
            session, recipient_phone=cust, recipient_role="CUSTOMER", related_order_id=order_id,
            message_text=(f"🔁 {n['kitchen_name']}: \"{counter_note.strip()}\"\n"
                          f"Reply 'accept' to add {_summarize(resolved)} for ₹{float(delta):.0f}, or 'no' to skip."))
        return {"status": "COUNTER_SENT", "message": "Counter sent to the customer."}

    return {"status": "INVALID", "message": "Decision must be accept, reject, or counter."}


@tool("respond_to_topup_request", args_schema=RespondToTopupRequestInput)
async def respond_to_topup_request(chef_phone: str, decision: str,
                                   counter_note: str | None = None, counter_items: list | None = None) -> str:
    """Chef: accept / reject / counter a customer's request to add extra items to a paid order."""
    async with transaction() as session:
        res = await _respond_to_topup_request(
            session, chef_phone=chef_phone, decision=decision,
            counter_note=counter_note, counter_items=counter_items)
        return res["message"]


# =============================================================================
# Resume handler: the customer answers a counter (Round 2, text-resumable)
# =============================================================================
@resume_handler("resolve_topup_counter")
async def resolve_topup_counter(phone: str, reply: dict[str, Any], ctx: dict[str, Any]) -> str:
    n = _topups.get(phone)
    if n is None or n["status"] != "WAITING_CUSTOMER":
        return "That add-on request already wrapped up."
    text = (reply.get("text") or "").strip().lower()
    accepted = any(w in text for w in ("accept", "yes", "ok", "sure", "take it", "deal"))
    if not accepted:
        n["status"] = "RESOLVED"
        clear_topup(phone)
        return "No worries — keeping your order as-is. 🙂"

    summary = _summarize(n["counter_items"])
    async with transaction() as session:
        mint = await _mint_and_arm_payment(session, n=n, items=n["counter_items"], amount=n["counter_amount"])
    if mint["status"] != "MINTED":
        n["status"] = "RESOLVED"
        clear_topup(phone)
        return "Sorry — I couldn't start the payment for that. Your order is unchanged."
    return (f"Great! Pay ₹{n['counter_amount']:.0f} to add {summary} to your order:\n"
            f"{mint['link']}\n\nOnce you pay, I'll add them.")


# =============================================================================
# Resume handler: the delta payment clears (out-of-band, fired by /pay)
# =============================================================================
async def _apply_topup_payment(session: AsyncSession, *, phone: str, txn: str | None = None) -> str:
    """Mark the TOPUP payment PAID (no cascade), append the items, notify the chef. Returns the summary."""
    n = _topups[phone]
    items = n["final_items"]
    # Mark the TOPUP payment PAID — NO order-status cascade (order is already past PENDING_PAYMENT).
    await execute_payment_status_update(
        session, payment_id=n["payment_id"], target_status="PAID",
        gateway_transaction_id=txn, cascade_confirm=False)
    # Append the agreed items to the existing order.
    await execute_append_items_to_order(session, order_id=n["order_id"], items=items)
    # One-off notice to the chef that the order changed.
    summary = _summarize(items)
    await execute_outbound_whatsapp_enqueue(
        session, recipient_phone=n["chef_phone"], recipient_role="CHEF", related_order_id=n["order_id"],
        message_text=f"➕ Order {n['order_id']} updated (paid) — added {summary}.")
    n["status"] = "RESOLVED"
    return summary


@resume_handler("confirm_topup_payment")
async def confirm_topup_payment(phone: str, reply: dict[str, Any], ctx: dict[str, Any]) -> str:
    n = _topups.get(phone)
    if n is None or n["status"] != "AWAITING_PAYMENT":
        return "That top-up already wrapped up."
    txn = (reply or {}).get("transaction_id") or (reply or {}).get("txn_id")
    order_id = n["order_id"]
    async with transaction() as session:
        summary = await _apply_topup_payment(session, phone=phone, txn=txn)
    clear_topup(phone)
    return f"✅ Payment received — {summary} added to your order {order_id}! The kitchen has been notified. 🍽️"
