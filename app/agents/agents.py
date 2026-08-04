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
from app.tools.customer_tools import (
    add_item_to_order,
    create_order,
    find_nearby_kitchens,
    get_customer_profile,
    register_customer,
    request_payment,
    view_cart,
    view_chef_menu,
)
from app.tools.master_tools import mint_payment_link, process_payment_webhook

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
)
MASTER_TOOLS: tuple[BaseTool, ...] = (
    mint_payment_link,
    process_payment_webhook,
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
chef_agent = Agent("CHEF", CHEF_PROMPT)
driver_agent = Agent("DRIVER", DRIVER_PROMPT)
master_agent = Agent("MASTER", MASTER_PROMPT, MASTER_TOOLS)

# Lookup by role (used by the router).
AGENTS: dict[str, Agent] = {
    a.role: a for a in (customer_agent, chef_agent, driver_agent, master_agent)
}
