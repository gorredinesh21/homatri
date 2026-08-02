"""LangGraph Shared State Schema (HomaatriGraphState).

Defines the core state schema passed across all agent nodes and tools in Homaatri.
"""

from __future__ import annotations

from typing import Any, Literal, Optional, Sequence, TypedDict
from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages
from typing_extensions import Annotated


class HomaatriGraphState(TypedDict):
    """Shared state dictionary passed between all nodes in the Homaatri multi-agent graph.

    Attributes:
        messages: Chronological conversation history list (managed via add_messages reducer).
        active_phone: Normalized 10-digit phone number of the active user.
        active_role: Active domain role ('CUSTOMER', 'CHEF', 'DRIVER', or 'MASTER').
        active_order_id: Currently active CustomerOrder ID (if applicable).
        hitl_session: Active SystemHitlSession status payload (if in WAITING status).
        error: System error payload if an unhandled exception occurs.
    """

    messages: Annotated[Sequence[BaseMessage], add_messages]
    active_phone: str
    active_role: Literal["CUSTOMER", "CHEF", "DRIVER", "MASTER"]
    active_order_id: Optional[str]
    hitl_session: Optional[dict[str, Any]]
    error: Optional[str]
