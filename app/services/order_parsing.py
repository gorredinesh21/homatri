"""Turn a free-text customer message into a structured, menu-resolved order.

Primary path: the LLM returns strict JSON (native json_mode). Fallback path: a
deterministic regex + fuzzy-match parser that needs no network, guaranteeing the
demo works fully offline. Either way, item names are resolved against the chef's
real menu via the fuzzy matcher before we trust them.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Sequence

from app.core.logging import get_logger
from app.services.llm import LLMUnavailable, llm
from app.services.menu_matcher import match_item
from app.schemas.parsing import ParsedItem, ParsedOrder

log = get_logger("parse")

_NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "a": 1, "an": 1, "couple": 2,
}


@dataclass
class ResolvedItem:
    menu_item: Any
    quantity: int


@dataclass
class OrderDraft:
    customer_name: str = ""
    items: list[ResolvedItem] = field(default_factory=list)
    unknown_items: list[str] = field(default_factory=list)
    delivery_time: str = ""
    delivery_address: str = ""
    is_valid: bool = False
    clarification: str = ""


def _menu_names(menu_items: Sequence[Any]) -> str:
    lines = []
    for m in menu_items:
        name = m["name"] if isinstance(m, dict) else m.name
        price = m["price"] if isinstance(m, dict) else m.price
        lines.append(f"- {name} (₹{price:g})")
    return "\n".join(lines)


async def parse_order(message: str, menu_items: Sequence[Any]) -> OrderDraft:
    parsed: ParsedOrder | None = None
    if llm.enabled:
        try:
            parsed = await _parse_with_llm(message, menu_items)
        except (LLMUnavailable, Exception) as e:  # noqa: BLE001
            log.warning("LLM parse failed, using offline parser: %s", e)
    if parsed is None:
        parsed = _parse_offline(message, menu_items)
    return _resolve(parsed, menu_items)


async def _parse_with_llm(message: str, menu_items: Sequence[Any]) -> ParsedOrder:
    system = (
        "You are Homaatri's order parser. Extract a food order from the customer "
        "message. Return ONLY a JSON object with this exact schema:\n"
        '{"customer_name": str, "items": [{"name": str, "quantity": int}], '
        '"delivery_time": str, "delivery_address": str, "is_valid": bool, '
        '"clarification": str}\n'
        "Rules: quantity defaults to 1. Map item names to the closest menu item "
        "below. If nothing on the menu is requested, set is_valid=false and put a "
        "short, polite clarification question. delivery_time like '8:30 PM' if "
        "mentioned (parse things like '8 30' as 8:30 PM). Only use menu items.\n\n"
        f"MENU:\n{_menu_names(menu_items)}"
    )
    data = await llm.chat_json(
        [{"role": "system", "content": system},
         {"role": "user", "content": message}],
        max_tokens=400,
    )
    return ParsedOrder.model_validate(data)


def _parse_offline(message: str, menu_items: Sequence[Any]) -> ParsedOrder:
    text = message.lower().strip()
    name = ""
    m = re.search(r"my name is ([a-z]+)", text) or re.search(r"i am ([a-z]+)", text)
    if m:
        name = m.group(1).capitalize()

    # Split into candidate item phrases.
    chunks = re.split(r",|\band\b|\bwith\b|\balso\b", text)
    items: list[ParsedItem] = []
    for chunk in chunks:
        chunk = chunk.strip()
        if not chunk:
            continue
        qty = 1
        mnum = re.search(r"\b(\d+)\b", chunk)
        if mnum:
            qty = int(mnum.group(1))
        else:
            for word, val in _NUMBER_WORDS.items():
                if re.search(rf"\b{word}\b", chunk):
                    qty = val
                    break
        # candidate name = chunk minus obvious command words
        cand = re.sub(r"\b(i|need|want|will|have|get|me|my|order|please|hey|the)\b", " ", chunk)
        cand = re.sub(r"\d+", " ", cand).strip()
        if len(cand) < 2:
            continue
        mm = match_item(cand, menu_items)
        if mm.matched:
            items.append(ParsedItem(name=cand, quantity=max(qty, 1)))

    dt = _extract_time(text)
    return ParsedOrder(
        customer_name=name,
        items=items,
        delivery_time=dt,
        is_valid=bool(items),
        clarification="" if items else "Sorry, I couldn't find those on the menu. Could you tell me what you'd like?",
    )


def _extract_time(text: str) -> str:
    m = re.search(r"\b(\d{1,2})[:\s](\d{2})\b", text)
    if m:
        hh, mm = int(m.group(1)), m.group(2)
        suffix = "PM" if hh < 12 else "PM"
        return f"{hh}:{mm} {suffix}"
    return ""


def _resolve(parsed: ParsedOrder, menu_items: Sequence[Any]) -> OrderDraft:
    draft = OrderDraft(
        customer_name=parsed.customer_name,
        delivery_time=parsed.delivery_time,
        delivery_address=parsed.delivery_address,
        clarification=parsed.clarification,
    )
    for it in parsed.items:
        mm = match_item(it.name, menu_items)
        if mm.matched:
            draft.items.append(ResolvedItem(menu_item=mm.menu_item, quantity=max(it.quantity, 1)))
        else:
            draft.unknown_items.append(it.name)
    draft.is_valid = bool(draft.items)
    if not draft.is_valid and not draft.clarification:
        draft.clarification = (
            "Sorry, I couldn't match that to our menu. What would you like to order?"
        )
    return draft
