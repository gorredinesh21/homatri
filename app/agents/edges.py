"""LangGraph Conditional Routing Edges (app/agents/edges.py).

Defines conditional switches:
1. route_by_role: Routes from master_router_node to master_agent_node, customer_agent_node, chef_agent_node, or driver_agent_node.
2. should_continue: Routes to ToolNode if AI requested tool calls, or END if turn is complete.
"""

from __future__ import annotations

from typing import Literal
from langchain_core.messages import AIMessage
from langgraph.graph import END

from app.agents.state import HomaatriGraphState


def route_by_role(
    state: HomaatriGraphState,
) -> Literal["master_agent_node", "customer_agent_node", "chef_agent_node", "driver_agent_node"]:
    """Direct graph conveyor belt to the appropriate domain agent node based on active_role."""
    role = state.get("active_role", "CUSTOMER")

    if role == "MASTER":
        return "master_agent_node"
    elif role == "CHEF":
        return "chef_agent_node"
    elif role == "DRIVER":
        return "driver_agent_node"
    else:
        return "customer_agent_node"


def should_continue(state: HomaatriGraphState) -> str:
    """Check if the last message contains tool calls. If yes, route to ToolNode ('tools'); otherwise END."""
    messages = state.get("messages", [])
    if not messages:
        return END

    last_message = messages[-1]
    if isinstance(last_message, AIMessage) and last_message.tool_calls:
        return "tools"

    return END
