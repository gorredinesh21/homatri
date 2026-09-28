"""The single Homaatri customer agent — LLM + one persona + its full toolset.

Homaatri used to run four WhatsApp agents (Customer / Chef / Driver / Master).
Chefs and riders now live entirely in their web portals, so exactly ONE agent
remains: the customer concierge that owns the whole conversation —
registration, browsing, ordering, payment, tracking, cancellation and feedback.

The payment gateway is no longer a separate agent either: `request_payment`
mints the Razorpay link deterministically, and confirmation arrives via the
payment poller / Razorpay webhook (see app/services/payment_confirmation.py).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from langchain_core.messages import BaseMessage, SystemMessage
from langchain_core.tools import BaseTool

from backend.app.agents.llm import shared_llm
from backend.app.agents.prompts import CUSTOMER_AGENT_PROMPT
from backend.app.tools.cancellation import cancel_order
from backend.app.tools.customer_tools import (
    add_item_to_order,
    add_special_instructions,
    check_my_payment,
    create_order,
    find_nearby_kitchens,
    get_customer_profile,
    get_order_status,
    register_customer,
    request_payment,
    submit_order_review,
    view_cart,
    view_chef_menu,
)
from backend.app.tools.master_tools import escalate_to_admin

# The one agent's complete toolset — it owns the whole customer conversation.
CUSTOMER_TOOLS: tuple[BaseTool, ...] = (
    get_customer_profile,
    find_nearby_kitchens,
    register_customer,
    view_chef_menu,
    create_order,
    add_item_to_order,
    add_special_instructions,
    view_cart,
    request_payment,
    check_my_payment,
    get_order_status,
    cancel_order,
    submit_order_review,
    escalate_to_admin,
)


@dataclass(frozen=True)
class Agent:
    """A Homaatri agent: a role + persona + its bound tool subset over the shared LLM."""

    role: str
    system_prompt: str
    tools: tuple[BaseTool, ...] = field(default_factory=tuple)

    @property
    def tool_map(self) -> dict[str, BaseTool]:
        """{tool_name: tool} for this agent — the harness/runtime binds from this."""
        return {t.name: t for t in self.tools}

    async def ainvoke(self, messages: list[BaseMessage]):
        """Invoke the agent's LLM (persona prepended, its tools bound)."""
        llm = shared_llm()
        if self.tools:
            llm = llm.bind_tools(list(self.tools))
        return await llm.ainvoke([SystemMessage(content=self.system_prompt), *messages])


# The single agent. (The old CHEF / DRIVER / MASTER personas are retired —
# chefs and riders use their web portals; payments run deterministically.)
customer_agent = Agent("CUSTOMER", CUSTOMER_AGENT_PROMPT, CUSTOMER_TOOLS)
