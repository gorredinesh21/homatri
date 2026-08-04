"""The four Homaatri agents — LLM + persona. NO tools bound yet.

Each agent is the shared Vertex AI client plus its own system prompt. Tools are
bound later, per flow (each agent will get only its own tool subset). For now an
agent can be invoked with a message list and it will reply using its persona.
"""

from __future__ import annotations

from dataclasses import dataclass

from langchain_core.messages import BaseMessage, SystemMessage

from app.agents.llm import shared_llm
from app.agents.prompts import (
    CHEF_PROMPT,
    CUSTOMER_PROMPT,
    DRIVER_PROMPT,
    MASTER_PROMPT,
)


@dataclass(frozen=True)
class Agent:
    """A Homaatri agent: a role + persona over the shared LLM. No tools bound yet."""

    role: str          # "CUSTOMER" | "CHEF" | "DRIVER" | "MASTER"
    system_prompt: str

    async def ainvoke(self, messages: list[BaseMessage]):
        """Invoke the agent's LLM with its persona prepended. (Tools bound later.)"""
        return await shared_llm().ainvoke([SystemMessage(content=self.system_prompt), *messages])


# The four agents — no tools bound.
customer_agent = Agent("CUSTOMER", CUSTOMER_PROMPT)
chef_agent = Agent("CHEF", CHEF_PROMPT)
driver_agent = Agent("DRIVER", DRIVER_PROMPT)
master_agent = Agent("MASTER", MASTER_PROMPT)

# Lookup by role (used later by the router).
AGENTS: dict[str, Agent] = {
    a.role: a for a in (customer_agent, chef_agent, driver_agent, master_agent)
}
