"""Pydantic schemas for LLM order parsing and modification intents."""
from __future__ import annotations

from pydantic import BaseModel, Field


class ParsedItem(BaseModel):
    name: str
    quantity: int = Field(default=1, ge=1)


class ParsedOrder(BaseModel):
    customer_name: str = ""
    items: list[ParsedItem] = Field(default_factory=list)
    delivery_time: str = ""       # free text, e.g. "8:30 PM"
    delivery_address: str = ""
    is_valid: bool = False
    clarification: str = ""       # message to send when is_valid is False


class ModificationIntent(BaseModel):
    """Result of interpreting an in-flight change request."""

    intent: str = "unknown"       # add_food | change_time | change_address | unknown
    items: list[ParsedItem] = Field(default_factory=list)
    delivery_time: str = ""
    delivery_address: str = ""
    reply: str = ""


class CustomerTurn(BaseModel):
    """Unified interpretation of one customer message when no order is in
    progress: is it an order or just conversation, plus a natural reply."""

    intent: str = "chat"          # "order" | "chat"
    customer_name: str = ""       # if the customer introduces themselves
    items: list[ParsedItem] = Field(default_factory=list)
    delivery_time: str = ""
    reply: str = ""               # warm, WhatsApp-length assistant message


class StaffIntent(BaseModel):
    """Interpretation of a chef/driver free-text message into an action.

    Chef actions:   start_cooking | mark_ready
    Driver actions: picked_up | delivered
    Otherwise:      chat
    """

    action: str = "chat"
    reply: str = ""
