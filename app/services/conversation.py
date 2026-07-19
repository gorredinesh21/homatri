"""Conversation orchestrator — the platform brain.

Takes a normalized inbound WhatsApp message and drives the whole cross-role
lifecycle: parse & create orders, issue payment links, interpret in-flight
modifications, notify chef/driver with interactive buttons, and advance order
state on button taps. Every side-effect (WhatsApp send, DB write, SSE publish)
flows through here so the three roles stay synchronized.

Interactive button ids follow ``<action>:<ORDER_CODE>`` so a tap in WhatsApp
(or the simulator) routes deterministically back to the right handler.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.logging import get_logger
from app.models.entities import Chef, Delivery, Driver, MenuItem, Order, User
from app.models.enums import (
    ChangeStatus,
    ChangeType,
    DeliveryStatus,
    OrderStatus,
    PaymentStatus,
    UserRole,
)
from app.services.events import bus
from app.payments.base import PaymentProvider
from app.payments.factory import get_payment_provider
from app.services import context as ctx
from app.services import order_lifecycle as lc
from app.services import policy
from app.services import rag, routing
from app.services.agent import Tool, run_agent
from app.services.llm import LLMUnavailable, llm
from app.services.menu_matcher import match_item
from app.services.order_parsing import OrderDraft, ResolvedItem
from app.services.role_chat import (
    ROLE_PROMPTS,
    interpret_customer_turn,
    interpret_modification,
    interpret_staff_turn,
    role_reply,
)
from app.services.state_snapshot import publish_state
from app.whatsapp.base import InboundMessage, WhatsAppProvider

log = get_logger("conv")

FALLBACK_DROPOFF_GPS = "12.9611,77.6387"  # demo customer location near kitchen

# Order states where the customer can still add food.
_FOOD_ADDABLE = (OrderStatus.CONFIRMED, OrderStatus.PREPARING)
# Order states considered "in the kitchen" (post-payment, pre-delivery).
_IN_KITCHEN = (
    OrderStatus.CONFIRMED,
    OrderStatus.PREPARING,
    OrderStatus.READY_FOR_PICKUP,
    OrderStatus.OUT_FOR_DELIVERY,
)


# ── User / entity resolution ──────────────────────────────────────────────────
async def _get_user(session: AsyncSession, phone: str) -> User | None:
    return (
        await session.execute(select(User).where(User.phone == phone))
    ).scalar_one_or_none()


async def _get_active_chef(session: AsyncSession) -> Chef | None:
    stmt = (
        select(Chef)
        .where(Chef.is_active.is_(True))
        .options(selectinload(Chef.menu_items), selectinload(Chef.user))
    )
    return (await session.execute(stmt)).scalars().first()


async def _get_available_driver(session: AsyncSession) -> Driver | None:
    stmt = (
        select(Driver)
        .where(Driver.is_available.is_(True))
        .options(selectinload(Driver.user))
    )
    return (await session.execute(stmt)).scalars().first()


async def _phone_of_chef(session: AsyncSession, chef_id) -> str:
    chef = (
        await session.execute(
            select(Chef).where(Chef.id == chef_id).options(selectinload(Chef.user))
        )
    ).scalar_one()
    return chef.user.phone


# ── Entry point ─────────────────────────────────────────────────────────────
async def process_inbound(
    session: AsyncSession,
    wa: WhatsAppProvider,
    pay: PaymentProvider,
    msg: InboundMessage,
) -> None:
    user = await _get_user(session, msg.from_phone)
    if user is None:
        # auto-register unknown senders as customers (WhatsApp-first onboarding)
        user = User(
            phone=msg.from_phone,
            name=msg.profile_name or "Customer",
            role=UserRole.CUSTOMER,
        )
        session.add(user)
        await session.flush()

    try:
        if msg.type == "interactive" and msg.reply_id:
            await _handle_action(session, wa, pay, user, msg.reply_id)
        elif msg.type == "location" and msg.latitude is not None:
            await _handle_location(session, wa, user, msg)
        elif msg.type == "text" and msg.text:
            await rag.add_memory(session, msg.from_phone, msg.text)
            await _handle_text(session, wa, pay, user, msg.text)
        else:
            await wa.send_text(user.phone, "Sorry, I couldn't read that message.")
    finally:
        await session.commit()
        await publish_state(session)


# ── Text routing by role ──────────────────────────────────────────────────────
async def _handle_text(
    session: AsyncSession,
    wa: WhatsAppProvider,
    pay: PaymentProvider,
    user: User,
    text: str,
) -> None:
    active = await lc.get_active_order_for_customer(session, user.phone)

    # Chef / driver -> staff handler (tool-agent, deterministic fallback).
    if user.role != UserRole.CUSTOMER:
        await _handle_staff(session, wa, user, text)
        return

    # Customer -> tool-calling agent when the LLM is available; the agent can
    # place orders and make any change via tools. Deterministic fallback below
    # keeps things working when the LLM is disabled or unreachable.
    if llm.enabled:
        try:
            await _run_customer_agent(session, wa, pay, user, text, active)
            return
        except LLMUnavailable:
            log.warning("customer agent unavailable; deterministic fallback")

    if active is not None and active.status in _IN_KITCHEN:
        await _handle_modification(session, wa, user, text, active)
    else:
        await _handle_customer_deterministic(session, wa, pay, user, text, active)


async def _handle_staff(
    session: AsyncSession, wa: WhatsAppProvider, user: User, text: str
) -> None:
    order = (
        await _get_chef_order(session)
        if user.role == UserRole.CHEF
        else await _get_driver_order(session)
    )
    if llm.enabled and order is not None:
        try:
            await _run_staff_agent(session, wa, user, text, order)
            return
        except LLMUnavailable:
            log.warning("staff agent unavailable; deterministic fallback")
    await _handle_staff_deterministic(session, wa, user, text, order)


async def _handle_customer_deterministic(
    session: AsyncSession,
    wa: WhatsAppProvider,
    pay: PaymentProvider,
    user: User,
    text: str,
    active: Order | None,
) -> None:
    chef = await _get_active_chef(session)
    if chef is None:
        await wa.send_text(user.phone, "Sorry, no kitchen is open right now.")
        return
    menu = chef.menu_items

    # RAG: pull this customer's relevant past turns to ground the reply.
    memories = await rag.query_memory(session, user.phone, text, top_n=4)
    turn = await interpret_customer_turn(
        text,
        menu,
        customer_name="" if user.name in ("", "Customer") else user.name,
        order_context=lc.build_active_order_context(active),
        memories=memories,
    )

    # Learn the customer's name if they introduced themselves.
    if turn.customer_name and user.name in ("", "Customer"):
        user.name = turn.customer_name

    if turn.intent == "order" and turn.items:
        resolved: list[ResolvedItem] = []
        for it in turn.items:
            mm = match_item(it.name, menu)
            if mm.matched:
                resolved.append(ResolvedItem(menu_item=mm.menu_item, quantity=it.quantity))
        if resolved:
            # Don't create a duplicate while one order still awaits payment.
            if active is not None and active.status == OrderStatus.AWAITING_PAYMENT:
                await wa.send_text(
                    user.phone,
                    f"You still have order {active.code} awaiting payment:\n"
                    f"{_order_summary(active)}\n\nTap to pay: {_pay_link(active)}",
                    preview_url=True,
                )
                return
            draft = OrderDraft(
                customer_name=turn.customer_name or user.name,
                items=resolved,
                delivery_time=turn.delivery_time,
            )
            await _create_order_and_bill(
                session, wa, pay, user, draft, preface=turn.reply
            )
            return

    # Conversational reply (greeting / question / clarification).
    reply = turn.reply or "How can I help with your order today?"
    await wa.send_text(user.phone, reply)
    await rag.add_memory(session, user.phone, f"assistant: {reply}")
    if active is not None:
        await _remember(session, active, "CUSTOMER", text)
        await _remember(session, active, "ASSISTANT", reply)


async def _get_chef_order(session: AsyncSession) -> Order | None:
    stmt = (
        select(Order)
        .where(Order.status.in_([
            OrderStatus.CONFIRMED, OrderStatus.PREPARING, OrderStatus.READY_FOR_PICKUP,
        ]))
        .order_by(Order.created_at.desc())
        .options(
            selectinload(Order.items), selectinload(Order.payment),
            selectinload(Order.delivery), selectinload(Order.change_requests),
        )
    )
    return (await session.execute(stmt)).scalars().first()


async def _get_driver_order(session: AsyncSession) -> Order | None:
    stmt = (
        select(Order)
        .join(Delivery, Delivery.order_id == Order.id)
        .where(Delivery.driver_id.isnot(None))
        .where(Order.status.in_([
            OrderStatus.READY_FOR_PICKUP, OrderStatus.OUT_FOR_DELIVERY,
        ]))
        .order_by(Order.created_at.desc())
        .options(
            selectinload(Order.items), selectinload(Order.payment),
            selectinload(Order.delivery), selectinload(Order.change_requests),
        )
    )
    return (await session.execute(stmt)).scalars().first()


_STAFF_ACTION_HANDLERS = {
    "start_cooking": "_act_cook_start",
    "mark_ready": "_act_mark_ready",
    "picked_up": "_act_driver_pickup",
    "delivered": "_act_driver_delivered",
}


async def _handle_staff_deterministic(
    session: AsyncSession,
    wa: WhatsAppProvider,
    user: User,
    text: str,
    order: Order | None,
) -> None:
    context = lc.build_active_order_context(order)

    if order is not None:
        intent = await interpret_staff_turn(text, user.role, context)
        handler_name = _STAFF_ACTION_HANDLERS.get(intent.action)
        if handler_name:
            handler = globals()[handler_name]
            try:
                await handler(session, wa, order)
            except lc.InvalidTransition:
                await wa.send_text(
                    user.phone,
                    f"That doesn't fit order {order.code}'s current state "
                    f"({order.status.value}).",
                )
                return
            if intent.reply:
                await wa.send_text(user.phone, intent.reply)
            await _remember(session, order, user.role.value, text)
            return

    # No actionable order, or plain conversation -> role chatbot.
    reply = await role_reply(session, user.role, user.phone, text, context)
    await wa.send_text(user.phone, reply)
    await _remember(session, order, user.role.value, text)
    await _remember(session, order, "ASSISTANT", reply)


# ── Relationship (customer↔chef↔driver) shared memory ─────────────────────────
async def _remember(session: AsyncSession, order: Order | None, role: str, text: str) -> None:
    if order is None or not text:
        return
    driver_id = order.delivery.driver_id if order.delivery else None
    await rag.remember_interaction(
        session,
        customer_id=order.customer_id,
        chef_id=order.chef_id,
        driver_id=driver_id,
        order_id=order.id,
        role=role,
        text=text,
    )


async def _recall_block(session: AsyncSession, order: Order | None, query: str) -> str:
    if order is None:
        return ""
    mems = await rag.recall_relationship(
        session, customer_id=order.customer_id, chef_id=order.chef_id,
        query=query, top_n=5,
    )
    return rag.build_relationship_block(mems)


# ── Tool-calling agents ───────────────────────────────────────────────────────
def _menu_text(menu) -> str:
    return "\n".join(
        f"- {(m['name'] if isinstance(m, dict) else m.name)} "
        f"(₹{(m['price'] if isinstance(m, dict) else m.price):g})"
        for m in menu
    )


def _resolve_items(items, menu) -> list[ResolvedItem]:
    """Resolve tool-supplied items to menu rows. Tolerates items given as plain
    strings (["Butter Roti"]) or objects ({"name","quantity"}), and clamps
    quantity to >= 1 (removals go through the dedicated remove tool)."""
    resolved: list[ResolvedItem] = []
    for it in items or []:
        if isinstance(it, str):
            name, qty = it, 1
        elif isinstance(it, dict):
            name = it.get("name", "") or it.get("item", "")
            try:
                qty = int(it.get("quantity", 1) or 1)
            except (TypeError, ValueError):
                qty = 1
        else:
            continue
        qty = max(1, abs(qty))
        mm = match_item(name, menu)
        if mm.matched:
            resolved.append(ResolvedItem(menu_item=mm.menu_item, quantity=qty))
    return resolved


_ITEMS_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "quantity": {"type": "integer", "minimum": 1},
                },
                "required": ["name"],
            },
        }
    },
    "required": ["items"],
}


async def _run_customer_agent(
    session: AsyncSession,
    wa: WhatsAppProvider,
    pay: PaymentProvider,
    user: User,
    text: str,
    active: Order | None,
) -> None:
    """Customer-facing tool-calling agent: places orders and makes changes."""
    chef = await _get_active_chef(session)
    if chef is None:
        await wa.send_text(user.phone, "Sorry, no kitchen is open right now.")
        return
    menu = chef.menu_items
    memories = await rag.query_memory(session, user.phone, text, top_n=4)
    # "authoritative" holds a system-authored customer reply for any transactional
    # action (order/total/status). When set, it OVERRIDES the model's prose so the
    # customer never sees a wrong amount or a false "chef accepted".
    box: dict[str, str | None] = {"link": None, "authoritative": None}

    async def _current() -> Order | None:
        return await lc.get_active_order_for_customer(session, user.phone)

    async def tool_get_menu() -> str:
        return "Today's menu:\n" + _menu_text(menu)

    async def tool_get_order_status() -> str:
        return lc.build_active_order_context(await _current())

    async def tool_place_order(items: list[dict], delivery_time: str = "") -> str:
        cur = await _current()
        if cur is not None and cur.status in (
            OrderStatus.PENDING, OrderStatus.AWAITING_PAYMENT
        ):
            box["link"] = _pay_link(cur)
            box["authoritative"] = (
                f"You have order {cur.code} awaiting payment.\nAmount to pay: ₹{cur.total:g}\n\n"
                f"Tap to pay securely: {box['link']}"
            )
            return f"Customer already has unpaid order {cur.code}; showed them the amount + pay link."
        resolved = _resolve_items(items, menu)
        if not resolved:
            return "None of those items are on the menu. Ask what they'd like."
        draft = OrderDraft(
            customer_name=user.name, items=resolved, delivery_time=delivery_time
        )
        order = await lc.create_order(session, user, chef, draft)
        if not order.delivery_gps:
            order.delivery_gps = FALLBACK_DROPOFF_GPS
        intent = await pay.create_payment(
            order_code=order.code, amount=order.total, currency="INR"
        )
        order.payment = _new_payment_row(order, intent)
        lc.set_status(order, OrderStatus.AWAITING_PAYMENT)
        await session.flush()
        box["link"] = _pay_link(order)
        box["authoritative"] = (
            f"Your order is placed! 🧾\nAmount to pay: ₹{order.total:g}\n\n"
            f"Tap to pay securely: {box['link']}\n\n(Your full bill arrives here once payment is done.)"
        )
        return (
            f"Order {order.code} created, total ₹{order.total:g}, STATUS: awaiting payment. "
            "The amount + pay link are already shown to the customer."
        )

    async def tool_add_items(items: list[dict]) -> str:
        cur = await _current()
        ok, reason = policy.can_add_food(cur)
        if not ok:
            return reason
        resolved = _resolve_items(items, menu)
        if not resolved:
            return "Those items aren't on the menu."
        desc = ", ".join(f"{r.quantity}x {_mi_name(r.menu_item)}" for r in resolved)

        # Not yet paid -> edit the order directly and refresh the pay link.
        if cur.status in (OrderStatus.PENDING, OrderStatus.AWAITING_PAYMENT):
            lc.add_food_items(cur, resolved)
            await session.flush()
            box["link"] = _pay_link(cur)
            box["authoritative"] = f"Added {desc}. New amount to pay: ₹{cur.total:g}\n\nTap to pay securely: {box['link']}"
            return f"Added {desc}; new total ₹{cur.total:g}. Confirmation shown to customer."

        # Already in the kitchen -> needs the chef to approve.
        lc.create_change_request(
            cur, ChangeType.FOOD,
            payload={"add": [{"name": _mi_name(r.menu_item), "qty": r.quantity} for r in resolved]},
            description=f"Add {desc}",
        )
        await session.flush()
        chef_phone = await _phone_of_chef(session, cur.chef_id)
        await wa.send_buttons(
            chef_phone,
            f"⚠️ CHANGE REQUEST for {cur.code}: add {desc}. Accept?",
            [(f"accept_food:{cur.code}", "Accept Change"),
             (f"reject_food:{cur.code}", "Reject")],
        )
        box["authoritative"] = (
            f"I've sent your request to add {desc} to the chef — I'll confirm the moment "
            "they approve it. (It isn't added to your order yet.)"
        )
        return (f"PENDING change request to add {desc} sent to the chef. NOT accepted yet — "
                "the customer has been told it's awaiting the chef's approval.")

    async def tool_remove_items(items) -> str:
        cur = await _current()
        ok, reason = policy.can_add_food(cur)  # same mutability window as adding
        if not ok:
            return reason
        resolved = _resolve_items(items, menu)  # quantity = amount to remove
        if not resolved:
            return "I couldn't tell which item to remove — which one should I take off?"
        removed = lc.remove_food_items(cur, resolved)
        if not removed:
            return "Those items weren't on the order."
        await session.flush()
        desc = ", ".join(removed)
        if cur.status in (OrderStatus.PENDING, OrderStatus.AWAITING_PAYMENT):
            box["link"] = _pay_link(cur)
            box["authoritative"] = f"Done — removed {desc}. New amount to pay: ₹{cur.total:g}\n\nTap to pay securely: {box['link']}"
            return f"Removed {desc}; new total ₹{cur.total:g}."
        chef_phone = await _phone_of_chef(session, cur.chef_id)
        await wa.send_text(
            chef_phone, f"ℹ️ Order {cur.code}: customer removed {desc}. New total ₹{cur.total:g}."
        )
        box["authoritative"] = f"Done — removed {desc}. Your new total is ₹{cur.total:g}."
        return f"Removed {desc}; new total ₹{cur.total:g}."

    async def tool_change_time(time: str) -> str:
        cur = await _current()
        ok, reason = policy.can_change_delivery(cur)
        if not ok:
            return reason
        cr = lc.create_change_request(
            cur, ChangeType.DELIVERY_TIME, payload={"time": time},
            description=f"Change delivery time to {time}",
        )
        await session.flush()
        if cur.delivery and cur.delivery.driver_id:
            drv = await _driver_phone(session, cur.delivery.driver_id)
            await wa.send_buttons(
                drv, f"⚠️ DELIVERY CHANGE for {cur.code}: new time {time}. Accept?",
                [(f"accept_change:{cur.code}", "Accept Time Change")],
            )
            box["authoritative"] = f"I've asked your rider to confirm the new delivery time ({time})."
            return f"PENDING: asked rider to confirm new time {time}; not applied yet."
        lc.apply_change_request(cur, cr)
        box["authoritative"] = f"Done — delivery time updated to {time}."
        return f"Delivery time updated to {time}."

    async def tool_change_address(address: str) -> str:
        cur = await _current()
        ok, reason = policy.can_change_delivery(cur)
        if not ok:
            return reason
        cr = lc.create_change_request(
            cur, ChangeType.DELIVERY_ADDRESS, payload={"address": address},
            description=f"Change delivery address to {address}",
        )
        await session.flush()
        if cur.delivery and cur.delivery.driver_id:
            drv = await _driver_phone(session, cur.delivery.driver_id)
            await wa.send_buttons(
                drv, f"⚠️ DELIVERY CHANGE for {cur.code}: new address {address}. Accept?",
                [(f"accept_change:{cur.code}", "Accept Change")],
            )
            box["authoritative"] = f"I've asked your rider to confirm the new delivery address."
            return f"PENDING: asked rider to confirm new address; not applied yet."
        lc.apply_change_request(cur, cr)
        box["authoritative"] = f"Done — delivery address updated to {address}."
        return f"Delivery address updated to {address}."

    async def tool_note_preference(note: str) -> str:
        """Record a dietary preference / allergy note WITHOUT touching the order."""
        note = (note or "").strip()
        if not note:
            return "No preference text provided."
        cur = await _current()
        if cur is not None:
            await rag.remember_interaction(
                session, customer_id=cur.customer_id, chef_id=cur.chef_id,
                driver_id=(cur.delivery.driver_id if cur.delivery else None),
                order_id=cur.id, role="PREFERENCE", text=note,
            )
            chef_phone = await _phone_of_chef(session, cur.chef_id)
            await wa.send_text(chef_phone, f"📝 Note from customer for {cur.code}: “{note}”")
            box["authoritative"] = f"Got it — I've noted “{note}” and passed it to the chef. 👍"
        else:
            await rag.add_memory(session, user.phone, f"preference: {note}")
            box["authoritative"] = f"Got it — I've noted “{note}”. 👍"
        return f"Recorded preference '{note}' and notified the chef. DID NOT change the order or price."

    async def tool_cancel_order(reason: str = "") -> str:
        cur = await _current()
        ok, why = policy.can_cancel(cur)
        if not ok:
            return why
        was_paid = bool(cur.payment and cur.payment.status == PaymentStatus.PAID)
        lc.set_status(cur, OrderStatus.CANCELLED)
        refund_txt = ""
        if was_paid:
            cur.payment.status = PaymentStatus.REFUNDED
            refund_txt = f" A refund of ₹{cur.total:g} has been initiated."
        chef_phone = await _phone_of_chef(session, cur.chef_id)
        await wa.send_text(chef_phone, f"❌ Order {cur.code} was cancelled by the customer.")
        if cur.delivery and cur.delivery.driver_id:
            drv = await _driver_phone(session, cur.delivery.driver_id)
            await wa.send_text(drv, f"❌ Order {cur.code} cancelled — no need to deliver it.")
        await session.flush()
        box["authoritative"] = f"Your order {cur.code} has been cancelled.{refund_txt} Sorry to see it go!"
        return f"Cancelled {cur.code} (was_paid={was_paid})."

    async def tool_track_order() -> str:
        cur = await _current()
        if cur is None:
            box["authoritative"] = "You don't have an active order right now."
            return "No active order."
        human = {
            "AWAITING_PAYMENT": "waiting for your payment",
            "CONFIRMED": "confirmed — the chef will start soon",
            "PREPARING": "being freshly cooked 👩‍🍳",
            "READY_FOR_PICKUP": "ready — a rider is being assigned",
            "OUT_FOR_DELIVERY": "on its way to you 🛵",
        }.get(cur.status.value, cur.status.value)
        route = ""
        if cur.status == OrderStatus.OUT_FOR_DELIVERY and cur.delivery and cur.delivery.route_url:
            route = f"\nLive route: {cur.delivery.route_url}"
        box["authoritative"] = f"Order {cur.code} is {human}.{route}"
        return f"Order {cur.code} status: {cur.status.value}."

    async def tool_get_customer_history() -> str:
        rows = (await session.execute(
            select(Order).where(Order.customer_id == user.id)
            .order_by(Order.created_at.desc()).options(selectinload(Order.items))
        )).scalars().all()
        if not rows:
            return "This customer has no past orders."
        return "Past orders:\n" + "\n".join(
            f"- {o.code} ({o.status.value}): " + ", ".join(f"{i.quantity}x {i.name}" for i in o.items)
            + f" — ₹{o.total:g}" for o in rows[:5]
        )

    async def tool_repeat_last_order() -> str:
        curnow = await _current()
        if curnow is not None and curnow.status in (OrderStatus.PENDING, OrderStatus.AWAITING_PAYMENT):
            box["link"] = _pay_link(curnow)
            box["authoritative"] = f"You still have order {curnow.code} awaiting payment.\nAmount: ₹{curnow.total:g}\n\nTap to pay: {box['link']}"
            return "Customer already has an unpaid order."
        prev = (await session.execute(
            select(Order).where(Order.customer_id == user.id)
            .order_by(Order.created_at.desc()).options(selectinload(Order.items))
        )).scalars().first()
        if prev is None or not prev.items:
            return "No previous order to repeat."
        resolved = []
        for it in prev.items:
            mm = match_item(it.name, menu)
            if mm.matched:
                resolved.append(ResolvedItem(menu_item=mm.menu_item, quantity=it.quantity))
        if not resolved:
            return "Couldn't rebuild the last order from today's menu."
        draft = OrderDraft(customer_name=user.name, items=resolved)
        order = await lc.create_order(session, user, chef, draft)
        if not order.delivery_gps:
            order.delivery_gps = FALLBACK_DROPOFF_GPS
        intent = await pay.create_payment(order_code=order.code, amount=order.total, currency="INR")
        order.payment = _new_payment_row(order, intent)
        lc.set_status(order, OrderStatus.AWAITING_PAYMENT)
        await session.flush()
        box["link"] = _pay_link(order)
        box["authoritative"] = f"Repeating your last order 🧾\nAmount to pay: ₹{order.total:g}\n\nTap to pay securely: {box['link']}"
        return f"Recreated last order as {order.code} (₹{order.total:g})."

    async def tool_escalate_to_human(reason: str) -> str:
        await bus.publish({"kind": "escalation", "phone": user.phone, "reason": reason})
        log.warning("ESCALATION from %s: %s", user.phone, reason)
        box["authoritative"] = (
            "I've flagged this to a Homaatri team member — someone will follow up with you "
            "shortly. 🙏"
        )
        return f"Escalated to a human: {reason}"

    async def tool_check_payment_status() -> str:
        cur = await _current()
        if cur is None:
            box["authoritative"] = "You don't have an active order right now."
            return "No active order."
        due = cur.balance_due
        if due <= 0 and cur.amount_paid > 0:
            box["authoritative"] = f"Order {cur.code} is fully paid ✅ (₹{cur.total:g})."
        else:
            box["link"] = _pay_link(cur)
            box["authoritative"] = f"Order {cur.code}: ₹{cur.amount_paid:g} paid, ₹{due:g} still due.\n\nTap to pay: {box['link']}"
        return f"paid ₹{cur.amount_paid:g}, due ₹{due:g}"

    async def tool_rate_order(stars: int, comment: str = "") -> str:
        fb = f"rated {int(stars)}/5" + (f": {comment}" if comment else "")
        cur = await _current()
        if cur is not None:
            await rag.remember_interaction(
                session, customer_id=cur.customer_id, chef_id=cur.chef_id,
                order_id=cur.id, role="FEEDBACK", text=fb,
            )
        else:
            await rag.add_memory(session, user.phone, f"feedback: {fb}")
        box["authoritative"] = f"Thank you for the {int(stars)}★ rating! 🙏 I've shared your feedback with the kitchen."
        return f"Recorded feedback: {fb}"

    async def tool_get_kitchen_status() -> str:
        c = await _get_active_chef(session)
        if not c:
            return "No kitchen is configured."
        avail = ", ".join(m.name for m in c.menu_items if m.available) or "nothing right now"
        box["authoritative"] = (
            f"{c.kitchen_name} is {'OPEN ✅' if c.is_active else 'CLOSED ⛔'}. "
            f"Available today: {avail}."
        )
        return f"kitchen open={c.is_active}"

    tools = [
        Tool("get_menu", "List today's available menu items and prices.",
             {"type": "object", "properties": {}}, tool_get_menu, is_action=False),
        Tool("get_order_status", "Get the customer's current order details/status.",
             {"type": "object", "properties": {}}, tool_get_order_status, is_action=False),
        Tool("place_order", "Create a new order from the menu items the customer wants.",
             {**_ITEMS_SCHEMA, "properties": {**_ITEMS_SCHEMA["properties"],
              "delivery_time": {"type": "string", "description": "e.g. '8:30 PM' if given"}}},
             tool_place_order),
        Tool("add_items", "Add items to the customer's in-progress (cooking) order.",
             _ITEMS_SCHEMA, tool_add_items),
        Tool("remove_items", "Remove items (or reduce quantity) from the customer's active order. quantity = how many to remove.",
             _ITEMS_SCHEMA, tool_remove_items),
        Tool("note_preference", "Record a dietary preference or allergy answer (e.g. 'no garlic', 'less spicy', 'allergic to nuts') and pass it to the chef. Use this for ALL preferences/allergies — it does NOT change the order or price.",
             {"type": "object", "properties": {"note": {"type": "string"}}, "required": ["note"]},
             tool_note_preference),
        Tool("cancel_order", "Cancel the customer's active order (refunds if already paid).",
             {"type": "object", "properties": {"reason": {"type": "string"}}}, tool_cancel_order),
        Tool("track_order", "Tell the customer where their order is / its current status + ETA.",
             {"type": "object", "properties": {}}, tool_track_order, is_action=False),
        Tool("get_customer_history", "Look up this customer's past orders (to personalise or answer 'what did I order last time').",
             {"type": "object", "properties": {}}, tool_get_customer_history, is_action=False),
        Tool("repeat_last_order", "Re-create the customer's most recent order as a new order to pay.",
             {"type": "object", "properties": {}}, tool_repeat_last_order),
        Tool("escalate_to_human", "Escalate to a human team member when the request is out of scope, a complaint, or you're unsure. Use sparingly.",
             {"type": "object", "properties": {"reason": {"type": "string"}}, "required": ["reason"]},
             tool_escalate_to_human),
        Tool("check_payment_status", "Tell the customer how much is paid / still due on their order.",
             {"type": "object", "properties": {}}, tool_check_payment_status, is_action=False),
        Tool("rate_order", "Record the customer's rating/feedback for their order (1-5 stars + optional comment).",
             {"type": "object", "properties": {"stars": {"type": "integer"}, "comment": {"type": "string"}}, "required": ["stars"]},
             tool_rate_order, is_action=False),
        Tool("get_kitchen_status", "Tell the customer if the kitchen is open and what's available.",
             {"type": "object", "properties": {}}, tool_get_kitchen_status, is_action=False),
        Tool("change_delivery_time", "Change the delivery time of the active order.",
             {"type": "object", "properties": {"time": {"type": "string"}}, "required": ["time"]},
             tool_change_time),
        Tool("change_delivery_address", "Change the delivery address of the active order.",
             {"type": "object", "properties": {"address": {"type": "string"}}, "required": ["address"]},
             tool_change_address),
    ]

    persona = (
        "You are Homaatri's assistant — a warm, sharp restaurant manager who is also "
        "the waiter and customer support, over WhatsApp. You take orders, make changes, "
        "and keep the customer happy while protecting the kitchen and rider. Use tools to "
        "act. Only call place_order when the customer names specific dishes; for a greeting, "
        "question, or vague 'yes', reply warmly WITHOUT a tool and offer the menu. Follow the "
        "POLICY for what's allowed at the current stage.\n"
        "STRICT ACCURACY RULES:\n"
        "- A dietary preference or allergy answer (e.g. 'no garlic', 'less spicy', 'no onion', "
        "'allergic to nuts') is NOT a food order. Call note_preference for it — NEVER "
        "add_items/place_order and NEVER change the total for a preference.\n"
        "- NEVER state prices, totals, or amounts yourself — the system shows the exact "
        "order summary and pay link automatically.\n"
        "- NEVER claim an action succeeded unless the tool result says so. If you add food "
        "to a cooking order, it is only a REQUEST to the chef — tell the customer it's "
        "awaiting the chef's approval, NOT that it's accepted/added.\n"
        "- Never invent menu items, order codes, or links. Keep replies WhatsApp-short."
    )
    system = await ctx.build_agent_context(
        session, persona=persona, order=active, menu=menu, query=text,
        customer_name=user.name,
    )
    result = await run_agent(system, text, tools)
    from app.services.llm import clean_llm_response

    if box.get("authoritative"):
        # A transactional action happened — use the system-authored, correct reply
        # (guarantees right totals/status; ignores any model paraphrase).
        reply = box["authoritative"]
    else:
        import re
        reply = clean_llm_response(result.text)
        reply = re.sub(r"https?://\S+", "", reply).strip()  # no hallucinated URLs in chit-chat
        if not reply:
            reply = "Done!" if result.acted else "How can I help with your order today?"

    await wa.send_text(user.phone, reply, preview_url=bool(box.get("link")))
    await rag.add_memory(session, user.phone, f"assistant: {reply}")
    # Record the turn in the trio's shared history (under the active/new order).
    order_now = await _current()
    if order_now is not None:
        await _remember(session, order_now, "CUSTOMER", text)
        await _remember(session, order_now, "ASSISTANT", reply)
        await ctx.bump_and_maybe_compact(session, order_now)


async def _run_staff_agent(
    session: AsyncSession,
    wa: WhatsAppProvider,
    user: User,
    text: str,
    order: Order,
) -> None:
    """Chef/driver tool-calling agent: advances the order via real tools."""
    context = lc.build_active_order_context(order)

    async def _wrap(act) -> str:
        try:
            await act(session, wa, order)
            return f"Done. Order {order.code} is now {order.status.value}."
        except lc.InvalidTransition as e:
            return f"Can't do that: {e}"

    async def tool_get_order() -> str:
        return context

    async def tool_send_message_to_customer(message: str) -> str:
        from app.models.entities import User
        from sqlalchemy import select
        cust = (
            await session.execute(
                select(User).where(User.id == order.customer_id)
            )
        ).scalar_one_or_none()
        if not cust:
            return "Customer account not found."
        await wa.send_text(cust.phone, message)
        await _remember(session, order, f"{user.role.value}_TO_CUSTOMER", message)
        return f"Message sent to customer {cust.name} on WhatsApp: '{message}'"

    async def _msg_customer(text_: str) -> None:
        cust = (await session.execute(select(User).where(User.id == order.customer_id))).scalar_one_or_none()
        if cust:
            await wa.send_text(cust.phone, text_)

    # ── shared staff tool ──
    async def tool_escalate(reason: str) -> str:
        await bus.publish({"kind": "escalation", "phone": user.phone, "reason": reason})
        log.warning("ESCALATION from %s (%s): %s", user.phone, user.role.value, reason)
        return "Flagged to a human team member — they'll follow up."

    # ── chef tools ──
    async def tool_mark_item_sold_out(item: str) -> str:
        chef = await _get_active_chef(session)
        mm = match_item(item, chef.menu_items) if chef else None
        if not mm or not mm.matched:
            return f"No menu item matches '{item}'."
        mm.menu_item.available = False
        await session.flush()
        return f"Marked {mm.menu_item.name} as SOLD OUT — customers can't order it now."

    async def tool_restock_item(item: str) -> str:
        chef = await _get_active_chef(session)
        mm = match_item(item, chef.menu_items) if chef else None
        if not mm or not mm.matched:
            return f"No menu item matches '{item}'."
        mm.menu_item.available = True
        await session.flush()
        return f"{mm.menu_item.name} is back on the menu."

    async def tool_set_kitchen_open(is_open: bool) -> str:
        chef = await _get_active_chef(session)
        if not chef:
            return "No kitchen found."
        chef.is_active = bool(is_open)
        await session.flush()
        return f"Kitchen is now {'OPEN ✅' if chef.is_active else 'CLOSED ⛔'}."

    async def tool_set_prep_estimate(minutes: int) -> str:
        if order is None:
            return "No active order."
        await _msg_customer(f"👩‍🍳 Update on {order.code}: your food will be ready in about {int(minutes)} minutes.")
        return f"Told the customer ~{int(minutes)} min prep time."

    # ── driver tools ──
    async def tool_report_delay(minutes: int) -> str:
        if order is None:
            return "No active order."
        await _msg_customer(f"🛵 Quick update on {order.code}: your rider is running ~{int(minutes)} min late. Thanks for your patience!")
        return f"Notified customer of ~{int(minutes)} min delay."

    async def tool_update_location(latitude: float, longitude: float) -> str:
        if order and order.delivery and order.delivery.driver_id:
            drv = (await session.execute(select(Driver).where(Driver.id == order.delivery.driver_id))).scalar_one_or_none()
            if drv:
                drv.current_gps_coordinates = f"{latitude},{longitude}"
                await session.flush()
                return "Location updated."
        return "No assigned delivery to update location for."

    async def tool_report_delivery_issue(issue: str) -> str:
        await bus.publish({"kind": "delivery_issue", "order": order.code if order else None, "issue": issue})
        if order:
            await _msg_customer(f"🛵 Your rider needs a hand with delivery: {issue}. Please reply here to help.")
        return f"Reported delivery issue + pinged the customer: {issue}"

    async def tool_accept_food() -> str:
        try:
            await _act_accept_food(session, wa, order)
        except lc.InvalidTransition as e:
            return f"Can't accept: {e}"
        due = order.balance_due
        if due > 0:
            return (f"Accepted the addition. The customer has ALREADY been sent a request "
                    f"to pay the extra ₹{due:g}; once they pay you'll get the final order "
                    "summary. Do NOT message the customer yourself — just confirm to the chef.")
        return "Accepted the addition (no extra charge). Customer already notified."

    async def tool_reject_food() -> str:
        try:
            await _act_reject_food(session, wa, order)
        except lc.InvalidTransition as e:
            return f"Can't: {e}"
        return "Rejected the change. The customer has ALREADY been notified — do not message them again."

    async def tool_get_order_queue() -> str:
        rows = (await session.execute(
            select(Order).where(Order.status.in_([
                OrderStatus.CONFIRMED, OrderStatus.PREPARING, OrderStatus.READY_FOR_PICKUP,
            ])).order_by(Order.created_at).options(selectinload(Order.items))
        )).scalars().all()
        if not rows:
            return "No active orders in the queue right now."
        return "Kitchen queue:\n" + "\n".join(
            f"- {o.code} ({o.status.value}): " + ", ".join(f"{i.quantity}x {i.name}" for i in o.items)
            for o in rows
        )

    async def tool_request_reassignment(reason: str = "") -> str:
        await bus.publish({"kind": "reassign_request", "order": order.code if order else None, "reason": reason})
        log.warning("driver requested reassignment for %s: %s", order.code if order else "?", reason)
        return f"Requested a rider reassignment for {order.code if order else 'the order'}. Ops has been notified."

    if user.role == UserRole.CHEF:
        tools = [
            Tool("start_cooking", "Mark that the chef has started cooking the order.",
                 {"type": "object", "properties": {}}, lambda: _wrap(_act_cook_start)),
            Tool("mark_ready", "Mark the order cooked and ready for pickup (assigns a rider).",
                 {"type": "object", "properties": {}}, lambda: _wrap(_act_mark_ready)),
            Tool("accept_food_change", "Accept the customer's pending request to add food. The system automatically asks the customer to pay the extra and notifies them.",
                 {"type": "object", "properties": {}}, tool_accept_food),
            Tool("reject_food_change", "Reject the customer's pending food-change request (system notifies the customer).",
                 {"type": "object", "properties": {}}, tool_reject_food),
            Tool("send_message_to_customer", "Send a message or question directly to the customer on WhatsApp (e.g. ask about food allergies, dietary restrictions, address confirmation).",
                 {"type": "object", "properties": {"message": {"type": "string"}}, "required": ["message"]}, tool_send_message_to_customer),
            Tool("mark_item_sold_out", "Mark a menu item as sold out so customers can't order it.",
                 {"type": "object", "properties": {"item": {"type": "string"}}, "required": ["item"]}, tool_mark_item_sold_out),
            Tool("restock_item", "Put a sold-out menu item back on the menu.",
                 {"type": "object", "properties": {"item": {"type": "string"}}, "required": ["item"]}, tool_restock_item),
            Tool("set_kitchen_open", "Open or close the kitchen for new orders.",
                 {"type": "object", "properties": {"is_open": {"type": "boolean"}}, "required": ["is_open"]}, tool_set_kitchen_open),
            Tool("set_prep_estimate", "Tell the customer roughly how many minutes until the food is ready.",
                 {"type": "object", "properties": {"minutes": {"type": "integer"}}, "required": ["minutes"]}, tool_set_prep_estimate),
            Tool("escalate_to_human", "Flag an issue to a human team member when out of scope or unsure.",
                 {"type": "object", "properties": {"reason": {"type": "string"}}, "required": ["reason"]}, tool_escalate),
            Tool("get_order", "Get the current order details.",
                 {"type": "object", "properties": {}}, tool_get_order, is_action=False),
        ]
        persona = ROLE_PROMPTS[UserRole.CHEF]
    else:
        tools = [
            Tool("mark_picked_up", "Mark that the rider has picked up the order.",
                 {"type": "object", "properties": {}}, lambda: _wrap(_act_driver_pickup)),
            Tool("mark_delivered", "Mark that the order has been delivered to the customer.",
                 {"type": "object", "properties": {}}, lambda: _wrap(_act_driver_delivered)),
            Tool("accept_delivery_change", "Accept the customer's pending delivery change.",
                 {"type": "object", "properties": {}}, lambda: _wrap(_act_accept_change)),
            Tool("send_message_to_customer", "Send a message or question directly to the customer on WhatsApp (e.g. ask about food allergies, delivery timing).",
                 {"type": "object", "properties": {"message": {"type": "string"}}, "required": ["message"]}, tool_send_message_to_customer),
            Tool("report_delay", "Tell the customer the delivery is running late by N minutes.",
                 {"type": "object", "properties": {"minutes": {"type": "integer"}}, "required": ["minutes"]}, tool_report_delay),
            Tool("update_location", "Update the rider's current GPS location.",
                 {"type": "object", "properties": {"latitude": {"type": "number"}, "longitude": {"type": "number"}}, "required": ["latitude", "longitude"]}, tool_update_location),
            Tool("report_delivery_issue", "Report a delivery problem (customer unreachable, wrong address) and ping the customer.",
                 {"type": "object", "properties": {"issue": {"type": "string"}}, "required": ["issue"]}, tool_report_delivery_issue),
            Tool("escalate_to_human", "Flag an issue to a human team member when out of scope or unsure.",
                 {"type": "object", "properties": {"reason": {"type": "string"}}, "required": ["reason"]}, tool_escalate),
            Tool("get_order", "Get the current order/delivery details.",
                 {"type": "object", "properties": {}}, tool_get_order, is_action=False),
        ]
        persona = ROLE_PROMPTS[UserRole.DRIVER]

    mediator = (
        persona
        + "\nYou are Homaatri's 3-way mediator between customer, chef and rider. Use a tool "
        "to advance the order when the staff member says something happened (started cooking, "
        "ready, picked up, delivered, accepting a change). If they ask to communicate with the "
        "customer (e.g. 'ask about allergies', 'tell them I'm 5 min away'), ALWAYS call "
        "send_message_to_customer — do NOT put customer-facing text in your reply to staff. "
        "When you accept_food_change or reject_food_change, the system ALREADY messages the "
        "customer (and asks them to pay any extra) — do NOT also call send_message_to_customer "
        "about it. Follow the POLICY. If they're just chatting, reply briefly without a tool."
    )
    system = await ctx.build_agent_context(
        session, persona=mediator, order=order, menu=None, query=text,
    )
    result = await run_agent(system, text, tools)
    from app.services.llm import clean_llm_response
    reply = clean_llm_response(result.text) if result.text else ("Updated." if result.acted else "Okay.")
    await wa.send_text(user.phone, reply)
    await _remember(session, order, user.role.value, text)
    await _remember(session, order, "ASSISTANT", reply)
    await ctx.bump_and_maybe_compact(session, order)


async def _create_order_and_bill(
    session: AsyncSession,
    wa: WhatsAppProvider,
    pay: PaymentProvider,
    user: User,
    draft: OrderDraft,
    preface: str = "",
) -> None:
    chef = await _get_active_chef(session)
    order = await lc.create_order(session, user, chef, draft)
    if not order.delivery_gps:
        order.delivery_gps = FALLBACK_DROPOFF_GPS
    intent = await pay.create_payment(
        order_code=order.code, amount=order.total, currency="INR"
    )
    order.payment = _new_payment_row(order, intent)
    lc.set_status(order, OrderStatus.AWAITING_PAYMENT)
    await session.flush()

    body = ""
    if preface:
        body += preface.strip() + "\n\n"
    body += f"{_order_summary(order)}\n\nTap to pay securely: {_pay_link(order)}"
    await wa.send_text(user.phone, body, preview_url=True)
    await rag.add_memory(
        session,
        user.phone,
        f"assistant: created order {order.code} -> "
        + ", ".join(f"{i.quantity}x {i.name}" for i in order.items),
    )
    # Seed the trio's shared history with the new order.
    await _remember(
        session, order, "CUSTOMER",
        "ordered " + ", ".join(f"{i.quantity}x {i.name}" for i in order.items),
    )
    return order


async def _handle_modification(
    session: AsyncSession,
    wa: WhatsAppProvider,
    user: User,
    text: str,
    order: Order,
) -> None:
    chef = await _get_active_chef(session)
    menu = chef.menu_items if chef else []
    await _remember(session, order, "CUSTOMER", text)
    intent = await interpret_modification(text, menu)

    if intent.intent == "add_food" and intent.items:
        resolved: list[ResolvedItem] = []
        for it in intent.items:
            mm = match_item(it.name, menu)
            if mm.matched:
                resolved.append(ResolvedItem(menu_item=mm.menu_item, quantity=it.quantity))
        if not resolved:
            await wa.send_text(user.phone, "I couldn't match that to the menu. What would you like to add?")
            return
        desc = ", ".join(f"{r.quantity}x {_mi_name(r.menu_item)}" for r in resolved)
        cr = lc.create_change_request(
            order, ChangeType.FOOD,
            payload={"add": [{"name": _mi_name(r.menu_item), "qty": r.quantity} for r in resolved]},
            description=f"Add {desc}",
        )
        await session.flush()
        # Notify chef with accept/reject buttons.
        chef_phone = await _phone_of_chef(session, order.chef_id)
        await wa.send_buttons(
            chef_phone,
            f"⚠️ CHANGE REQUEST for {order.code}: add {desc}. Accept?",
            [(f"accept_food:{order.code}", "Accept Change"),
             (f"reject_food:{order.code}", "Reject")],
        )
        await wa.send_text(user.phone, f"Sent your request to add {desc} to the chef. I'll confirm shortly!")
        return

    if intent.intent == "change_time" and intent.delivery_time:
        cr = lc.create_change_request(
            order, ChangeType.DELIVERY_TIME,
            payload={"time": intent.delivery_time},
            description=f"Change delivery time to {intent.delivery_time}",
        )
        await session.flush()
        if order.delivery and order.delivery.driver_id:
            drv_phone = (await _driver_phone(session, order.delivery.driver_id))
            await wa.send_buttons(
                drv_phone,
                f"⚠️ DELIVERY CHANGE for {order.code}: new time {intent.delivery_time}. Accept?",
                [(f"accept_change:{order.code}", "Accept Time Change")],
            )
            await wa.send_text(user.phone, "Sent the new time to your rider for confirmation.")
        else:
            lc.apply_change_request(order, cr)
            await wa.send_text(user.phone, f"Done! Delivery time updated to {intent.delivery_time}.")
        return

    if intent.intent == "change_address":
        await wa.send_text(user.phone, intent.reply or "Please share your new delivery address or location.")
        return

    # general chat
    context = lc.build_active_order_context(order)
    reply = await role_reply(session, user.role, user.phone, text, context)
    await wa.send_text(user.phone, reply)


async def _handle_location(
    session: AsyncSession, wa: WhatsAppProvider, user: User, msg: InboundMessage
) -> None:
    order = await lc.get_active_order_for_customer(session, user.phone)
    if order is None:
        await wa.send_text(user.phone, "Thanks! Send us your order and we'll use this location for delivery.")
        return
    order.delivery_gps = f"{msg.latitude},{msg.longitude}"
    if order.delivery:
        order.delivery.dropoff_gps = order.delivery_gps
    await wa.send_text(user.phone, "📍 Got your delivery location, thanks!")


# ── Button-tap actions ─────────────────────────────────────────────────────────
async def _handle_action(
    session: AsyncSession,
    wa: WhatsAppProvider,
    pay: PaymentProvider,
    user: User,
    reply_id: str,
) -> None:
    action, _, code = reply_id.partition(":")
    order = await lc.get_order_by_code(session, code)
    if order is None:
        await wa.send_text(user.phone, "That order could not be found.")
        return

    handlers = {
        "cook_start": _act_cook_start,
        "mark_ready": _act_mark_ready,
        "accept_food": _act_accept_food,
        "reject_food": _act_reject_food,
        "accept_change": _act_accept_change,
        "driver_pickup": _act_driver_pickup,
        "driver_delivered": _act_driver_delivered,
    }
    handler = handlers.get(action)
    if handler is None:
        await wa.send_text(user.phone, "Unknown action.")
        return
    await handler(session, wa, order)


async def _act_cook_start(session, wa, order: Order) -> None:
    lc.set_status(order, OrderStatus.PREPARING)
    await _notify_customer(session, wa, order, "👩‍🍳 Your food is now being prepared!")
    # Always hand the chef the next action so they're never stuck.
    chef_phone = await _phone_of_chef(session, order.chef_id)
    await wa.send_buttons(
        chef_phone,
        f"Order {order.code} is cooking. Tap when it's ready (or just message me 'order is ready').",
        [(f"mark_ready:{order.code}", "Ready for Pickup")],
    )


async def _act_mark_ready(session, wa, order: Order) -> None:
    # Tolerate a chef who marks ready without first tapping "cooking started".
    if order.status == OrderStatus.CONFIRMED:
        lc.set_status(order, OrderStatus.PREPARING)
    lc.set_status(order, OrderStatus.READY_FOR_PICKUP)
    driver = await _get_available_driver(session)
    chef = await _get_active_chef(session)
    if driver and chef:
        delivery = order.delivery or Delivery(order_id=order.id)
        delivery.driver_id = driver.id
        delivery.status = DeliveryStatus.ASSIGNED
        delivery.pickup_gps = chef.gps_coordinates
        delivery.dropoff_gps = order.delivery_gps or FALLBACK_DROPOFF_GPS
        dispatch = routing.build_dispatch(
            chef.gps_coordinates,
            [routing.Stop(order.customer_name or "Customer", delivery.dropoff_gps)],
        )
        delivery.route_url = dispatch["route_url"]
        order.delivery = delivery
        await session.flush()
        # Send rider the job: text + drop-off location pin + accept button.
        await wa.send_text(
            driver.user.phone,
            f"🛵 New delivery {order.code}\nPickup: {chef.kitchen_name}\n"
            f"Drop: {order.delivery_address or 'see pin'}\n"
            f"Route ({dispatch['total_km']} km): {dispatch['route_url']}",
            preview_url=True,
        )
        await wa.send_location(
            driver.user.phone,
            *delivery.dropoff_gps.split(","),
            name="Delivery drop-off",
            address=order.delivery_address or None,
        )
        await wa.send_buttons(
            driver.user.phone,
            f"Order {order.code} ready for pickup. Accept?",
            [(f"driver_pickup:{order.code}", "Accept & Picked Up")],
        )
    await _notify_customer(session, wa, order, "✅ Your order is ready and a rider is being assigned!")


async def _act_accept_food(session, wa, order: Order) -> None:
    chef = await _get_active_chef(session)
    menu = chef.menu_items if chef else []
    pending = [c for c in order.change_requests
               if c.status == ChangeStatus.PENDING and c.change_type == ChangeType.FOOD]
    added_names = []
    for cr in pending:
        resolved = []
        for entry in cr.payload.get("add", []):
            mm = match_item(entry["name"], menu)
            if mm.matched:
                qty = entry.get("qty", 1)
                resolved.append(ResolvedItem(menu_item=mm.menu_item, quantity=qty))
                added_names.append(f"{qty}x {mm.menu_item.name}")
        lc.add_food_items(order, resolved)
        cr.status = ChangeStatus.ACCEPTED
    await session.flush()

    item_desc = ", ".join(added_names) if added_names else "items"
    delta = order.balance_due  # total - amount already paid

    if delta > 0:
        # Ask the customer to pay ONLY the extra. The final summary goes to the
        # chef AFTER this top-up is paid (handled in on_payment_success).
        pay = get_payment_provider()
        intent = await pay.create_payment(order_code=order.code, amount=delta, currency="INR")
        if order.payment:
            order.payment.provider_order_id = intent.provider_order_id
        await session.flush()
        await _notify_customer(
            session, wa, order,
            f"👍 The chef accepted your addition ({item_desc})!\n"
            f"Extra amount to pay: ₹{delta:g}\n\nTap to pay: {_pay_link(order)}",
        )
    else:
        # No extra owed — confirm to customer and send the chef the final summary now.
        await _notify_customer(
            session, wa, order,
            f"👍 The chef accepted your addition ({item_desc}). No extra charge — you're all set!",
        )
        await _send_chef_final_summary(session, wa, order)


async def _act_reject_food(session, wa, order: Order) -> None:
    for cr in order.change_requests:
        if cr.status == ChangeStatus.PENDING and cr.change_type == ChangeType.FOOD:
            cr.status = ChangeStatus.REJECTED
    await _notify_customer(session, wa, order, "Sorry, the chef couldn't accommodate that change.")


async def _act_accept_change(session, wa, order: Order) -> None:
    for cr in order.change_requests:
        if cr.status == ChangeStatus.PENDING and cr.change_type in (
            ChangeType.DELIVERY_TIME, ChangeType.DELIVERY_ADDRESS
        ):
            lc.apply_change_request(order, cr)
    await session.flush()
    await _notify_customer(session, wa, order, "👍 Your rider confirmed the delivery change.")


async def _act_driver_pickup(session, wa, order: Order) -> None:
    lc.set_status(order, OrderStatus.OUT_FOR_DELIVERY)
    if order.delivery:
        order.delivery.status = DeliveryStatus.PICKED_UP
        drv_phone = await _driver_phone(session, order.delivery.driver_id)
        await wa.send_buttons(
            drv_phone,
            f"Order {order.code} picked up. Mark delivered when done.",
            [(f"driver_delivered:{order.code}", "Mark Delivered")],
        )
    await _notify_customer(session, wa, order, "🛵 Your order is out for delivery!")


async def _act_driver_delivered(session, wa, order: Order) -> None:
    # Tolerate a rider who marks delivered without first tapping "picked up".
    if order.status == OrderStatus.READY_FOR_PICKUP:
        lc.set_status(order, OrderStatus.OUT_FOR_DELIVERY)
    lc.set_status(order, OrderStatus.DELIVERED)
    if order.delivery:
        order.delivery.status = DeliveryStatus.DELIVERED
    await _notify_customer(session, wa, order, "🎉 Your order has been delivered. Enjoy your meal!")


# ── Payment success (called from the payment webhook) ──────────────────────────
async def on_payment_success(
    session: AsyncSession, wa: WhatsAppProvider, order: Order
) -> None:
    # ── TOP-UP payment: order already in the kitchen, customer paid the extra
    # for an accepted addition. Record it and send the chef the FINAL summary.
    if order.status not in (OrderStatus.PENDING, OrderStatus.AWAITING_PAYMENT):
        if order.balance_due > 0:
            order.amount_paid = order.total
            if order.payment:
                order.payment.status = PaymentStatus.PAID
            await session.flush()
            await _notify_customer(
                session, wa, order,
                f"💳 Top-up received — thank you! Your updated order:\n\n{_order_summary(order)}",
            )
            await _send_chef_final_summary(session, wa, order)
        else:
            log.info("duplicate payment for %s ignored (nothing due)", order.code)
        await publish_state(session)
        return

    # ── INITIAL payment: AWAITING_PAYMENT -> CONFIRMED.
    if order.payment:
        order.payment.status = PaymentStatus.PAID
    order.amount_paid = order.total
    lc.set_status(order, OrderStatus.CONFIRMED)
    await session.flush()
    chef_phone = await _phone_of_chef(session, order.chef_id)
    checklist = "\n".join(f"  • {i.quantity}x {i.name}" for i in order.items)
    await wa.send_buttons(
        chef_phone,
        f"✅ NEW CONFIRMED ORDER {order.code}\n{checklist}\n"
        f"Deliver by {order.requested_delivery_time}. Start cooking?",
        [(f"cook_start:{order.code}", "Cooking Started"),
         (f"mark_ready:{order.code}", "Ready for Pickup")],
    )
    await _notify_customer(
        session, wa, order,
        f"💳 Payment received — thank you!\n\n{_order_summary(order)}\n\n"
        "👩‍🍳 The chef has been notified and will start preparing your order.",
    )
    await publish_state(session)


async def _send_chef_final_summary(session: AsyncSession, wa: WhatsAppProvider, order: Order) -> None:
    """Send the chef the up-to-date order checklist (after an accepted + paid add-on)."""
    chef_phone = await _phone_of_chef(session, order.chef_id)
    checklist = "\n".join(f"  • {i.quantity}x {i.name}" for i in order.items)
    await wa.send_text(
        chef_phone,
        f"📋 UPDATED ORDER {order.code} (customer paid for the addition):\n{checklist}\n"
        f"Total ₹{order.total:g}. Please prepare the updated order.",
    )


# ── helpers ─────────────────────────────────────────────────────────────────
def _new_payment_row(order: Order, intent) -> "object":
    from app.models.entities import Payment
    from app.models.enums import PaymentStatus

    return Payment(
        order_id=order.id,
        provider=intent.provider,
        provider_order_id=intent.provider_order_id,
        amount=intent.amount,
        currency=intent.currency,
        status=PaymentStatus.CREATED,
    )


async def _driver_phone(session: AsyncSession, driver_id) -> str:
    drv = (
        await session.execute(
            select(Driver).where(Driver.id == driver_id).options(selectinload(Driver.user))
        )
    ).scalar_one()
    return drv.user.phone


async def _customer_phone(session: AsyncSession, order: Order) -> str:
    u = (
        await session.execute(select(User).where(User.id == order.customer_id))
    ).scalar_one()
    return u.phone


async def _notify_customer(session, wa, order: Order, message: str) -> None:
    phone = await _customer_phone(session, order)
    await wa.send_text(phone, message)


def _mi_name(mi) -> str:
    return mi["name"] if isinstance(mi, dict) else mi.name


def _order_summary(order: Order) -> str:
    lines = [f"🧾 Order {order.code}"]
    for it in order.items:
        lines.append(f"  {it.quantity}x {it.name} — ₹{it.line_total:g}")
    lines.append(f"Delivery: ₹{order.delivery_fee:g}")
    lines.append(f"Total: ₹{order.total:g}")
    return "\n".join(lines)


def _public() -> str:
    from app.core.config import settings

    return settings.public_base_url


def _pay_link(order: Order) -> str:
    # The /pay page renders the summary and drives demo checkout or the real
    # Razorpay handshake, so it's the single link we hand the customer.
    return f"{_public()}/pay/{order.code}"
