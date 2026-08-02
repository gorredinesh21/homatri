"""LangGraph Compiled Multi-Agent State Machine (app/agents/graph.py).

Assembles nodes, tools, conditional routing edges, and checkpointer into a compiled graph app (homatri_app).
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode

from app.agents.edges import route_by_role, should_continue
from app.agents.nodes import (
    CHEF_TOOLS,
    CUSTOMER_TOOLS,
    DRIVER_TOOLS,
    MASTER_TOOLS,
    chef_agent_node,
    customer_agent_node,
    driver_agent_node,
    master_agent_node,
    master_router_node,
)
from app.agents.state import HomaatriGraphState

# Combine all domain tools into a unified ToolNode
ALL_SYSTEM_TOOLS = MASTER_TOOLS + CUSTOMER_TOOLS + CHEF_TOOLS + DRIVER_TOOLS
tool_node = ToolNode(tools=ALL_SYSTEM_TOOLS)

# Construct State Graph
builder = StateGraph(HomaatriGraphState)

# Add Nodes
builder.add_node("master_router_node", master_router_node)
builder.add_node("master_agent_node", master_agent_node)
builder.add_node("customer_agent_node", customer_agent_node)
builder.add_node("chef_agent_node", chef_agent_node)
builder.add_node("driver_agent_node", driver_agent_node)
builder.add_node("tools", tool_node)

# Set Entry Point
builder.add_edge(START, "master_router_node")

# Add Conditional Edge from Router -> Domain Agent Nodes
builder.add_conditional_edges(
    "master_router_node",
    route_by_role,
    {
        "master_agent_node": "master_agent_node",
        "customer_agent_node": "customer_agent_node",
        "chef_agent_node": "chef_agent_node",
        "driver_agent_node": "driver_agent_node",
    },
)

# Add Conditional Edges from Domain Agents -> ToolNode or END
for node_name in ["master_agent_node", "customer_agent_node", "chef_agent_node", "driver_agent_node"]:
    builder.add_conditional_edges(
        node_name,
        should_continue,
        {
            "tools": "tools",
            END: END,
        },
    )

# Add Edge from ToolNode back to Router
builder.add_edge("tools", "master_router_node")

# Compile Graph
homatri_app = builder.compile()
