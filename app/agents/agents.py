"""The four Homaatri agents — LLM + persona + their bound tool subset.

Each agent is the shared LLM plus its own system prompt and the tools it owns.
Binding is per flow: an agent only ever holds its own tools (a spoke can read any
table but writes only its own; cross-domain writes go Master -> owner executor).

Bound so far:
  - CUSTOMER: Flows 1-4 customer tools.
  - MASTER:   Flow 4 gateway tools (mint_payment_link, process_payment_webhook).
  - CHEF / DRIVER: none yet (their spokes are unbuilt).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from langchain_core.messages import BaseMessage, SystemMessage
from langchain_core.tools import BaseTool

from app.agents.llm import shared_llm
from app.agents.prompts import (
    CHEF_PROMPT,
    CUSTOMER_PROMPT,
    DRIVER_PROMPT,
    MASTER_PROMPT,
)
from app.tools.cancellation import cancel_order, respond_to_cancellation
from app.tools.chef_tools import (
    get_chef_batch,
    get_chef_profile,
    mark_order_ready,
    set_daily_capacity,
    toggle_dish_stock,
)
from app.tools.customer_tools import (
    add_item_to_order,
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
from app.tools.dietary import (
    relay_dietary_request,
    request_dietary_change,
    respond_to_dietary_request,
)
from app.tools.driver_tools import (
    ask_chef_status,
    confirm_delivery,
    confirm_pickup,
    get_driver_profile,
    get_driver_route,
    report_address_issue,
    respond_to_driver_query,
    update_duty_status,
)
from app.tools.master_tools import (
    escalate_to_admin,
    get_kitchen_availability_summary,
    get_order_pipeline_summary,
    mint_payment_link,
    process_payment_webhook,
)

# Per-agent tool subsets (each agent owns only its own tools).
CUSTOMER_TOOLS: tuple[BaseTool, ...] = (
    get_customer_profile,
    find_nearby_kitchens,
    register_customer,
    view_chef_menu,
    create_order,
    add_item_to_order,
    view_cart,
    request_payment,
    get_order_status,
    request_dietary_change,
    cancel_order,
    submit_order_review,
    escalate_to_admin,
)
CHEF_TOOLS: tuple[BaseTool, ...] = (
    get_chef_profile,
    get_chef_batch,
    toggle_dish_stock,
    set_daily_capacity,
    mark_order_ready,
    respond_to_dietary_request,
    respond_to_cancellation,
    respond_to_driver_query,
    escalate_to_admin,
)
DRIVER_TOOLS: tuple[BaseTool, ...] = (
    get_driver_profile,
    update_duty_status,
    get_driver_route,
    confirm_pickup,
    confirm_delivery,
    ask_chef_status,
    report_address_issue,
    escalate_to_admin,
)
MASTER_TOOLS: tuple[BaseTool, ...] = (
    mint_payment_link,
    process_payment_webhook,
    relay_dietary_request,
    escalate_to_admin,
    get_kitchen_availability_summary,
    get_order_pipeline_summary,
)


@dataclass(frozen=True)
class Agent:
    """A Homaatri agent: a role + persona + its bound tool subset over the shared LLM."""

    role: str          # "CUSTOMER" | "CHEF" | "DRIVER" | "MASTER"
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


# The four agents, with their tools bound.
customer_agent = Agent("CUSTOMER", CUSTOMER_PROMPT, CUSTOMER_TOOLS)
chef_agent = Agent("CHEF", CHEF_PROMPT, CHEF_TOOLS)
driver_agent = Agent("DRIVER", DRIVER_PROMPT, DRIVER_TOOLS)
master_agent = Agent("MASTER", MASTER_PROMPT, MASTER_TOOLS)

# Lookup by role (used by the router).
AGENTS: dict[str, Agent] = {
    a.role: a for a in (customer_agent, chef_agent, driver_agent, master_agent)
}
