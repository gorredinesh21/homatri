"""Role-aware chatbot replies + modification-intent interpretation.

Each role (customer / chef / driver) gets a distinct persona, and every prompt
is enriched with the live order snapshot plus RAG conversation memory so all
three sides stay synchronized. All LLM calls have deterministic offline
fallbacks so the demo never hard-fails.
"""
from __future__ import annotations

import re
from typing import Any, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.enums import UserRole
from app.schemas.parsing import (
    CustomerTurn,
    ModificationIntent,
    ParsedItem,
    StaffIntent,
)
from app.services import rag
from app.services.llm import LLMUnavailable, llm

log = get_logger("role_chat")

ROLE_PROMPTS = {
    UserRole.CUSTOMER: (
        "You are Homaatri's friendly WhatsApp support assistant for a home-cook "
        "food service. Be warm, concise (WhatsApp-length), and helpful about the "
        "customer's order, menu, delivery time and changes. Never invent menu "
        "items or prices."
    ),
    UserRole.CHEF: (
        "You are Homaatri's kitchen assistant helping a home chef. Be brief and "
        "practical about the current order checklist, changes to accept, and "
        "cooking status."
    ),
    UserRole.DRIVER: (
        "You are Homaatri's rider dispatcher assistant. Be brief and practical "
        "about pickup, drop-off address, route and delivery timing."
    ),
}

_MOD_KEYWORDS = {
    "add_food": ["add", "more", "also want", "extra", "one more", "another"],
    "change_time": ["time", "deliver at", "delivery at", "earlier", "later", "postpone"],
    "change_address": ["address", "location", "deliver to", "different place"],
}


async def role_reply(
    session: AsyncSession,
    role: UserRole,
    phone: str,
    message: str,
    context: str,
) -> str:
    """Generate a role-appropriate reply, enriched with memory + order context."""
    memories = await rag.query_memory(session, phone, message, top_n=3)
    mem_block = rag.build_context_block(memories)
    system = f"{ROLE_PROMPTS[role]}\n\n{context}"
    if mem_block:
        system += f"\n\n{mem_block}"
    if llm.enabled:
        try:
            return await llm.chat(
                [{"role": "system", "content": system},
                 {"role": "user", "content": message}],
                max_tokens=200,
                temperature=0.4,
            )
        except (LLMUnavailable, Exception) as e:  # noqa: BLE001
            log.warning("role_reply fallback: %s", e)
    return _offline_reply(role)


def _offline_reply(role: UserRole) -> str:
    if role == UserRole.CUSTOMER:
        return "Got it! I've noted that. Anything else I can help with for your order?"
    if role == UserRole.CHEF:
        return "Noted. Check the order checklist on your screen."
    return "Noted. Please follow the route to complete the delivery."


def _menu_block(menu_items: Sequence[Any]) -> str:
    lines = []
    for m in menu_items:
        name = m["name"] if isinstance(m, dict) else m.name
        price = m["price"] if isinstance(m, dict) else m.price
        lines.append(f"- {name} (₹{price:g})")
    return "\n".join(lines)


async def interpret_customer_turn(
    message: str,
    menu_items: Sequence[Any],
    *,
    customer_name: str,
    order_context: str,
    memories: list[str],
) -> CustomerTurn:
    """Decide whether a customer message is an order or conversation, and craft
    a warm, context-aware reply. This is what makes the bot behave like a real
    assistant instead of blindly parsing every message as an order."""
    if llm.enabled:
        try:
            return await _customer_turn_llm(
                message, menu_items, customer_name, order_context, memories
            )
        except (LLMUnavailable, Exception) as e:  # noqa: BLE001
            log.warning("customer turn LLM fallback: %s", e)
    return _customer_turn_offline(message, menu_items, customer_name)


async def _customer_turn_llm(
    message: str,
    menu_items: Sequence[Any],
    customer_name: str,
    order_context: str,
    memories: list[str],
) -> CustomerTurn:
    mem_block = rag.build_context_block(memories)
    known = customer_name or "unknown"
    system = (
        "You are Homaatri's friendly WhatsApp assistant for a home-kitchen food "
        "service in India. Chat naturally and warmly, WhatsApp-length.\n"
        f"Customer name: {known}.\n"
        f"{order_context}\n"
        + (f"{mem_block}\n" if mem_block else "")
        + f"\nTODAY'S MENU:\n{_menu_block(menu_items)}\n\n"
        "Return ONLY JSON with this schema: "
        '{"intent":"order|chat","customer_name":str,'
        '"items":[{"name":str,"quantity":int}],"delivery_time":str,"reply":str}\n'
        "RULES:\n"
        "- intent=\"order\" ONLY if the customer clearly names specific dish(es) "
        "they want to order right now. A greeting, thanks, question, or a vague "
        "'yes'/'ok' with no dish named is intent=\"chat\" (items empty).\n"
        "- Always write a helpful 'reply': greet by name if known, offer/mention "
        "the menu, answer questions, or confirm the order. Never invent menu items.\n"
        "- If the customer states their name, put it in customer_name.\n"
        "- Parse times like '8 30' as '8:30 PM'."
    )
    data = await llm.chat_json(
        [{"role": "system", "content": system},
         {"role": "user", "content": message}],
        max_tokens=350,
    )
    return CustomerTurn.model_validate(data)


def _customer_turn_offline(
    message: str, menu_items: Sequence[Any], customer_name: str
) -> CustomerTurn:
    # Reuse the deterministic order parser; if it finds real menu items, it's an
    # order, otherwise it's conversation with a helpful menu-aware greeting.
    from app.services.order_parsing import _parse_offline

    parsed = _parse_offline(message, menu_items)
    if parsed.is_valid and parsed.items:
        return CustomerTurn(
            intent="order",
            customer_name=parsed.customer_name or customer_name,
            items=parsed.items,
            delivery_time=parsed.delivery_time,
            reply="Great choice! Let me put that together for you.",
        )
    name = parsed.customer_name or customer_name
    greeting = f"Hi {name}! " if name and name != "Customer" else "Hi there! "
    return CustomerTurn(
        intent="chat",
        customer_name=parsed.customer_name,
        reply=(
            f"{greeting}Welcome to Homaatri 🍲 Here's today's menu:\n"
            f"{_menu_block(menu_items)}\n\nJust tell me what you'd like "
            "(e.g. '2 butter roti and 1 dal fry')."
        ),
    )


_CHEF_ACTIONS = {
    "start_cooking": ["start", "cooking", "begin", "preparing", "on it", "making"],
    "mark_ready": ["ready", "done", "cooked", "finished", "prepared", "complete",
                   "pack", "pickup", "pick up", "ready for pickup"],
}
_DRIVER_ACTIONS = {
    "picked_up": ["picked", "pick up", "collected", "got it", "on my way",
                  "leaving", "picked up", "out for delivery"],
    "delivered": ["delivered", "dropped", "drop off", "handed", "complete",
                  "done", "reached", "gave"],
}


async def interpret_staff_turn(
    message: str, role: UserRole, order_context: str
) -> StaffIntent:
    """Interpret a chef/driver message into an executable action (or chat).

    This is what makes the kitchen/rider assistants *agentic* — they don't just
    chat, they can advance the order when the staff member says so in words.
    """
    if role not in (UserRole.CHEF, UserRole.DRIVER):
        return StaffIntent(action="chat")
    if llm.enabled:
        try:
            return await _staff_turn_llm(message, role, order_context)
        except (LLMUnavailable, Exception) as e:  # noqa: BLE001
            log.warning("staff turn LLM fallback: %s", e)
    return _staff_turn_offline(message, role)


async def _staff_turn_llm(
    message: str, role: UserRole, order_context: str
) -> StaffIntent:
    if role == UserRole.CHEF:
        actions = (
            'Actions: "start_cooking" (chef began cooking), "mark_ready" (food is '
            'cooked and ready for pickup), or "chat".'
        )
        persona = ROLE_PROMPTS[UserRole.CHEF]
    else:
        actions = (
            'Actions: "picked_up" (rider collected the food from the kitchen), '
            '"delivered" (rider handed the food to the customer), or "chat".'
        )
        persona = ROLE_PROMPTS[UserRole.DRIVER]
    system = (
        f"{persona}\n{order_context}\n\n{actions}\n"
        'Return ONLY JSON: {"action": "<one of the actions>", "reply": "<short '
        'natural confirmation or answer>"}. Choose an action ONLY if the message '
        "clearly signals it happened/should happen; otherwise action=\"chat\"."
    )
    data = await llm.chat_json(
        [{"role": "system", "content": system},
         {"role": "user", "content": message}],
        max_tokens=150,
    )
    return StaffIntent.model_validate(data)


def _staff_turn_offline(message: str, role: UserRole) -> StaffIntent:
    text = message.lower()
    table = _CHEF_ACTIONS if role == UserRole.CHEF else _DRIVER_ACTIONS
    # Check the more-advanced action first (mark_ready / delivered) so "done"
    # resolves to completion, not the earlier step.
    order = (["mark_ready", "start_cooking"] if role == UserRole.CHEF
             else ["delivered", "picked_up"])
    for action in order:
        if any(kw in text for kw in table[action]):
            return StaffIntent(action=action, reply="Got it, updating the order.")
    return StaffIntent(action="chat")


async def interpret_modification(
    message: str, menu_items: Sequence[Any]
) -> ModificationIntent:
    """Classify an in-flight change request into a structured intent."""
    if llm.enabled:
        try:
            return await _interpret_llm(message, menu_items)
        except (LLMUnavailable, Exception) as e:  # noqa: BLE001
            log.warning("modification LLM fallback: %s", e)
    return _interpret_offline(message)


async def _interpret_llm(message: str, menu_items: Sequence[Any]) -> ModificationIntent:
    names = ", ".join(
        (m["name"] if isinstance(m, dict) else m.name) for m in menu_items
    )
    system = (
        "You interpret a customer's request to modify an EXISTING food order. "
        "Return ONLY JSON: {\"intent\": \"add_food|change_time|change_address|chat\", "
        "\"items\": [{\"name\": str, \"quantity\": int}], \"delivery_time\": str, "
        "\"delivery_address\": str, \"reply\": str}. "
        "Parse times like '8 30' as '8:30 PM'. Only use these menu items for "
        f"add_food: {names}. If it's not a modification, intent='chat'."
    )
    data = await llm.chat_json(
        [{"role": "system", "content": system},
         {"role": "user", "content": message}],
        max_tokens=250,
    )
    return ModificationIntent.model_validate(data)


def _interpret_offline(message: str) -> ModificationIntent:
    text = message.lower()
    # time
    mtime = re.search(r"\b(\d{1,2})[:\s](\d{2})\b", text)
    if any(k in text for k in _MOD_KEYWORDS["change_time"]) and mtime:
        return ModificationIntent(
            intent="change_time",
            delivery_time=f"{int(mtime.group(1))}:{mtime.group(2)} PM",
            reply="Sure, I'll request that delivery time change.",
        )
    if any(k in text for k in _MOD_KEYWORDS["add_food"]):
        qty = 1
        mnum = re.search(r"\b(\d+)\b", text)
        if mnum:
            qty = int(mnum.group(1))
        # crude item extraction: text after 'add'
        m = re.search(r"add\s+(?:\d+\s+)?(.*)", text)
        name = (m.group(1) if m else text).strip()
        return ModificationIntent(
            intent="add_food",
            items=[ParsedItem(name=name, quantity=qty)],
            reply="Sure, I'll send that addition to the chef.",
        )
    if any(k in text for k in _MOD_KEYWORDS["change_address"]):
        return ModificationIntent(
            intent="change_address",
            reply="Please share the new delivery address.",
        )
    return ModificationIntent(intent="chat", reply="")
