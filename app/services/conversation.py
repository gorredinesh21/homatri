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
    UserRole,
)
from app.payments.base import PaymentProvider
from app.services import order_lifecycle as lc
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


def _resolve_items(items: list[dict], menu) -> list[ResolvedItem]:
    resolved: list[ResolvedItem] = []
    for it in items or []:
        name = it.get("name", "")
        qty = int(it.get("quantity", 1) or 1)
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
    box: dict[str, str | None] = {"link": None}

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
            return (
                f"Customer already has order {cur.code} awaiting payment "
                f"(total ₹{cur.total:g}). Give them this link: {box['link']}"
            )
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
        return (
            f"Created order {order.code}. {_order_summary(order)}. "
            f"Payment link: {box['link']}"
        )

    async def tool_add_items(items: list[dict]) -> str:
        cur = await _current()
        if cur is None or cur.status not in _FOOD_ADDABLE:
            return "There's no order currently being prepared, so nothing to add to."
        resolved = _resolve_items(items, menu)
        if not resolved:
            return "Those items aren't on the menu."
        desc = ", ".join(f"{r.quantity}x {_mi_name(r.menu_item)}" for r in resolved)
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
        return f"Sent a request to the chef to add {desc}. Awaiting their approval."

    async def tool_change_time(time: str) -> str:
        cur = await _current()
        if cur is None:
            return "No active order to change."
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
            return f"Sent the new time {time} to the rider for confirmation."
        lc.apply_change_request(cur, cr)
        return f"Delivery time updated to {time}."

    async def tool_change_address(address: str) -> str:
        cur = await _current()
        if cur is None:
            return "No active order to change."
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
            return f"Sent the new address to the rider for confirmation."
        lc.apply_change_request(cur, cr)
        return f"Delivery address updated to {address}."

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
        Tool("change_delivery_time", "Change the delivery time of the active order.",
             {"type": "object", "properties": {"time": {"type": "string"}}, "required": ["time"]},
             tool_change_time),
        Tool("change_delivery_address", "Change the delivery address of the active order.",
             {"type": "object", "properties": {"address": {"type": "string"}}, "required": ["address"]},
             tool_change_address),
    ]

    mem = rag.build_context_block(memories)
    shared = await _recall_block(session, active, text)
    system = (
        "You are Homaatri's warm, concise WhatsApp assistant for a home-kitchen "
        f"food service. The customer's name is {user.name}.\n"
        f"{lc.build_active_order_context(active)}\n"
        + (f"{mem}\n" if mem else "")
        + (f"{shared}\n" if shared else "")
        + f"\nMENU:\n{_menu_text(menu)}\n\n"
        "Use tools to place orders or make changes. Only call place_order when the "
        "customer clearly names dishes to order. For greetings, questions, or a "
        "vague 'yes', just reply warmly WITHOUT calling a tool (offer the menu). "
        "Never invent menu items. Keep replies WhatsApp-short. When you place an "
        "order, include the payment link from the tool result in your reply."
    )
    result = await run_agent(system, text, tools)
    reply = result.text.strip() if result.text else ""
    if not reply:
        reply = "Done!" if result.acted else "How can I help with your order today?"
    # Guarantee the exact payment link survives (never let the model drop it).
    if box["link"] and box["link"] not in reply:
        reply = f"{reply}\n\nTap to pay securely: {box['link']}"
    await wa.send_text(user.phone, reply, preview_url=bool(box["link"]))
    await rag.add_memory(session, user.phone, f"assistant: {reply}")
    # Record the turn in the trio's shared history (under the active/new order).
    order_now = await _current()
    if order_now is not None:
        await _remember(session, order_now, "CUSTOMER", text)
        await _remember(session, order_now, "ASSISTANT", reply)


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

    if user.role == UserRole.CHEF:
        tools = [
            Tool("start_cooking", "Mark that the chef has started cooking the order.",
                 {"type": "object", "properties": {}}, lambda: _wrap(_act_cook_start)),
            Tool("mark_ready", "Mark the order cooked and ready for pickup (assigns a rider).",
                 {"type": "object", "properties": {}}, lambda: _wrap(_act_mark_ready)),
            Tool("accept_food_change", "Accept the customer's pending request to add food.",
                 {"type": "object", "properties": {}}, lambda: _wrap(_act_accept_food)),
            Tool("reject_food_change", "Reject the customer's pending food-change request.",
                 {"type": "object", "properties": {}}, lambda: _wrap(_act_reject_food)),
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
            Tool("get_order", "Get the current order/delivery details.",
                 {"type": "object", "properties": {}}, tool_get_order, is_action=False),
        ]
        persona = ROLE_PROMPTS[UserRole.DRIVER]

    shared = await _recall_block(session, order, text)
    system = (
        f"{persona}\n{context}\n"
        + (f"{shared}\n" if shared else "")
        + "\nUse a tool to update the order when the staff member says something "
        "happened (e.g. cooking started, food ready, picked up, delivered, or "
        "accepting a change). If they're just chatting or asking, reply briefly "
        "WITHOUT calling a tool."
    )
    result = await run_agent(system, text, tools)
    reply = result.text.strip() if result.text else ("Updated." if result.acted else "Okay.")
    await wa.send_text(user.phone, reply)
    await _remember(session, order, user.role.value, text)
    await _remember(session, order, "ASSISTANT", reply)


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
    for cr in pending:
        resolved = []
        for entry in cr.payload.get("add", []):
            mm = match_item(entry["name"], menu)
            if mm.matched:
                resolved.append(ResolvedItem(menu_item=mm.menu_item, quantity=entry.get("qty", 1)))
        lc.add_food_items(order, resolved)
        cr.status = ChangeStatus.ACCEPTED
    await session.flush()
    await _notify_customer(
        session, wa, order,
        f"👍 The chef accepted your change. Updated total: ₹{order.total:g}.",
    )


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
    if order.payment:
        from app.models.enums import PaymentStatus

        order.payment.status = PaymentStatus.PAID
    lc.set_status(order, OrderStatus.CONFIRMED)
    await session.flush()
    # Notify chef with the confirmed checklist + start-cooking button.
    chef_phone = await _phone_of_chef(session, order.chef_id)
    checklist = "\n".join(f"  • {i.quantity}x {i.name}" for i in order.items)
    await wa.send_buttons(
        chef_phone,
        f"✅ NEW CONFIRMED ORDER {order.code}\n{checklist}\n"
        f"Deliver by {order.requested_delivery_time}. Start cooking?",
        [(f"cook_start:{order.code}", "Cooking Started"),
         (f"mark_ready:{order.code}", "Ready for Pickup")],
    )
    await _notify_customer(session, wa, order, f"💳 Payment received for {order.code}! The chef has been notified.")
    await publish_state(session)


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
