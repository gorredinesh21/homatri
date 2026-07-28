"""
===============================================================================
Homaatri Multi-Agent Orchestration Demo (LangGraph + Zero Circular Imports)
===============================================================================
This file demonstrates how LangGraph decouples agent nodes and tools.
Tools do NOT import other agents directly. Instead:
1. A Tool executes its DB action and returns a structured event payload.
2. The Agent Node places the target routing signal into the Graph State (`target_node`).
3. The Central StateGraph Router Edge evaluates `target_node` and transitions execution safely!
===============================================================================
"""

from typing import TypedDict, Annotated, List, Dict, Any
from langchain_core.messages import BaseMessage, SystemMessage, HumanMessage
from langgraph.graph import StateGraph, END


# =============================================================================
# 1. SHARED GRAPH STATE DEFINITION
# =============================================================================
class HomaatriState(TypedDict):
    messages: List[Dict[str, Any]]
    sender_phone: str
    current_role: str
    target_node: str              # Next node to route to ("master_node", "driver_node", "END")
    event_payload: Dict[str, Any]  # Cross-agent event data


# =============================================================================
# 2. DECOUPLED TOOL DEFINITIONS (Zero Agent Imports!)
# =============================================================================

def chef_mark_order_packed_ready_tool(chef_phone: str, order_id: str) -> Dict[str, Any]:
    """
    Chef Tool: Marks order packed in DB and emits an event payload targeting Master Agent.
    Notice: NO IMPORTS of Master or Driver agent!
    """
    print(f"\n   [TOOL EXECUTION] Chef ({chef_phone}) marked Order #{order_id} as PACKED_READY in DB.")
    
    # Return structured event payload (Decoupled Handoff)
    return {
        "event_type": "ORDER_PACKED_READY",
        "order_id": order_id,
        "chef_phone": chef_phone,
        "assigned_driver_phone": "+919988776655",
        "next_target": "master_node"  # Signal for Graph Router
    }


def master_relay_order_ready_to_driver_tool(order_id: str, assigned_driver_phone: str) -> Dict[str, Any]:
    """
    Master Tool: Receives ready payload, finds assigned driver, and routes to Driver Agent.
    Notice: NO IMPORTS of Driver agent!
    """
    print(f"   [TOOL EXECUTION] Master Agent processed Order #{order_id} ready signal.")
    print(f"   [TOOL EXECUTION] Master Agent identified assigned Driver ({assigned_driver_phone}).")
    
    return {
        "event_type": "DRIVER_PICKUP_NOTIFICATION",
        "order_id": order_id,
        "driver_phone": assigned_driver_phone,
        "pickup_location": "Ramesh Home Kitchen, Hitech City",
        "next_target": "driver_node"  # Signal for Graph Router
    }


def driver_accept_pickup_notification_tool(driver_phone: str, order_id: str, location: str) -> Dict[str, Any]:
    """
    Driver Tool: Updates stop status in DB and dispatches single-leg navigation link.
    """
    print(f"   [TOOL EXECUTION] Driver ({driver_phone}) received pickup alert for Order #{order_id}.")
    print(f"   [TOOL EXECUTION] Single-Leg Navigation Link dispatched: https://maps.google.com/?daddr={location}")
    
    return {
        "status": "EN_ROUTE_TO_KITCHEN",
        "next_target": "END"  # Execution complete!
    }


# =============================================================================
# 3. AGENT NODE DEFINITIONS
# =============================================================================

def chef_node(state: HomaatriState) -> HomaatriState:
    print("\n--- 👨‍🍳 NODE: CHEF AGENT NODE ---")
    print(f"   Processing turn for Chef: {state['sender_phone']}")
    
    # Execute Chef Tool (e.g. Chef marked order ready)
    tool_result = chef_mark_order_packed_ready_tool(
        chef_phone=state['sender_phone'], 
        order_id="ord_104"
    )
    
    # Update State with next target node
    return {
        **state,
        "target_node": tool_result["next_target"],
        "event_payload": tool_result
    }


def master_node(state: HomaatriState) -> HomaatriState:
    print("\n--- 👑 NODE: MASTER AGENT (SUPERVISOR / MEDIATOR) NODE ---")
    payload = state.get("event_payload", {})
    print(f"   Master Agent handling event: '{payload.get('event_type')}'")
    
    # Execute Master Relay Tool
    tool_result = master_relay_order_ready_to_driver_tool(
        order_id=payload.get("order_id", "ord_104"),
        assigned_driver_phone=payload.get("assigned_driver_phone", "+919988776655")
    )
    
    return {
        **state,
        "target_node": tool_result["next_target"],
        "event_payload": tool_result
    }


def driver_node(state: HomaatriState) -> HomaatriState:
    print("\n--- 🚴‍♂️ NODE: DELIVERY DRIVER AGENT NODE ---")
    payload = state.get("event_payload", {})
    print(f"   Driver Agent handling event: '{payload.get('event_type')}'")
    
    # Execute Driver Tool
    tool_result = driver_accept_pickup_notification_tool(
        driver_phone=payload.get("driver_phone", "+919988776655"),
        order_id=payload.get("order_id", "ord_104"),
        location=payload.get("pickup_location", "Ramesh Kitchen")
    )
    
    return {
        **state,
        "target_node": "END",
        "event_payload": tool_result
    }


def customer_node(state: HomaatriState) -> HomaatriState:
    print("\n--- 🙋‍♂️ NODE: CUSTOMER AGENT NODE ---")
    return {**state, "target_node": "END"}


# =============================================================================
# 4. CENTRAL GRAPH ROUTER (Zero Circular Imports)
# =============================================================================

def route_next_agent(state: HomaatriState) -> str:
    """
    Central Router Edge: Reads state["target_node"] and decides which agent runs next!
    """
    target = state.get("target_node", "END")
    if target == "master_node":
        return "master_node"
    elif target == "driver_node":
        return "driver_node"
    elif target == "customer_node":
        return "customer_node"
    elif target == "chef_node":
        return "chef_node"
    return END


# Build the Graph
builder = StateGraph(HomaatriState)

# Add all agent nodes to graph
builder.add_node("chef_node", chef_node)
builder.add_node("master_node", master_node)
builder.add_node("driver_node", driver_node)
builder.add_node("customer_node", customer_node)

# Set Entry Point
builder.set_entry_point("chef_node")

# Add Conditional Routing Edges from each node
builder.add_conditional_edges("chef_node", route_next_agent)
builder.add_conditional_edges("master_node", route_next_agent)
builder.add_conditional_edges("driver_node", route_next_agent)
builder.add_conditional_edges("customer_node", route_next_agent)

# Compile Graph
homaatri_graph = builder.compile()


# =============================================================================
# 5. LIVE SIMULATION EXECUTION
# =============================================================================
if __name__ == "__main__":
    print("=======================================================================")
    print("🚀 STARTING LIVE MULTI-AGENT EXECUTION SIMULATION")
    print("Scenario: Chef messages 'Order #104 is packed and ready for pickup'")
    print("=======================================================================")

    initial_state: HomaatriState = {
        "messages": [{"role": "user", "content": "Order #104 is ready"}],
        "sender_phone": "+919876543210",  # Chef Phone Number
        "current_role": "CHEF",
        "target_node": "chef_node",
        "event_payload": {}
    }

    # Execute Graph
    final_state = homaatri_graph.invoke(initial_state)

    print("\n=======================================================================")
    print("✅ MULTI-AGENT EXECUTION COMPLETED SUCCESSFULLY WITH ZERO ERRORS!")
    print("Execution Chain Completed: ChefNode ➔ MasterNode ➔ DriverNode ➔ END")
    print("=======================================================================")
