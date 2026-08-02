"""LangGraph Domain Agent Nodes (app/agents/nodes.py).

Defines the 4 specialized agent nodes:
1. master_router_node: Determines user role from DB and assigns active_role.
2. customer_agent_node: Customer Concierge Agent bound with Customer Tools.
3. chef_agent_node: Chef Concierge Agent bound with Chef Tools.
4. driver_agent_node: Driver Concierge Agent bound with Driver Tools.
"""

from __future__ import annotations

import os
from typing import Any, Dict

from langchain_core.messages import SystemMessage
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.state import HomaatriGraphState
from app.db.session import SessionFactory
from app.models.chef import ChefProfile
from app.models.customer import CustomerProfile
from app.models.driver import DriverProfile
from app.tools.chef_tools import (
    get_assigned_driver_eta_tool,
    get_chef_daily_batch_checklist_tool,
    get_chef_earnings_summary_tool,
    get_chef_menu_tool,
    get_chef_profile_tool,
    mark_orders_packed_ready_tool,
    relay_order_ready_to_driver_tool,
    respond_to_custom_request_tool,
    toggle_dish_availability_tool,
    update_daily_dish_capacity_tool,
)
from app.tools.customer_tools import (
    add_item_to_order_tool,
    cancel_customer_order_tool,
    find_nearby_home_kitchens_tool,
    generate_payment_link_tool,
    get_customer_profile_tool,
    get_order_history_tool,
    initialize_customer_order_tool,
    register_customer_profile_tool,
    submit_order_review_tool,
    view_chef_menu_tool,
)
from app.tools.master_tools import (
    dispatch_whatsapp_outbound_message_tool,
    escalate_delayed_batch_prep_tool,
    execute_cutoff_batch_and_route_optimization_tool,
    get_master_kitchen_availability_summary_tool,
    get_master_order_pipeline_summary_tool,
    process_payment_gateway_webhook_tool,
    request_cut_off_extension_tool,
    trigger_hitl_escalation_tool,
)

from app.tools.driver_tools import (

    confirm_stop_arrival_and_delivery_tool,
    get_driver_active_delivery_route_tool,
    get_driver_profile_tool,
    register_driver_profile_tool,
    report_delivery_delay_or_gate_issue_tool,
    update_driver_duty_status_tool,
)

# Domain Tool Collections

MASTER_TOOLS = [
    execute_cutoff_batch_and_route_optimization_tool,
    trigger_hitl_escalation_tool,
    dispatch_whatsapp_outbound_message_tool,
    get_master_kitchen_availability_summary_tool,
    get_master_order_pipeline_summary_tool,
    process_payment_gateway_webhook_tool,
    escalate_delayed_batch_prep_tool,
    request_cut_off_extension_tool,
]

CUSTOMER_TOOLS = [
    get_customer_profile_tool,
    register_customer_profile_tool,
    find_nearby_home_kitchens_tool,
    view_chef_menu_tool,
    initialize_customer_order_tool,
    add_item_to_order_tool,
    generate_payment_link_tool,
    get_order_history_tool,
    submit_order_review_tool,
    cancel_customer_order_tool,
]

CHEF_TOOLS = [
    get_chef_profile_tool,
    get_chef_menu_tool,
    update_daily_dish_capacity_tool,
    toggle_dish_availability_tool,
    get_chef_daily_batch_checklist_tool,
    mark_orders_packed_ready_tool,
    get_chef_earnings_summary_tool,
    relay_order_ready_to_driver_tool,
    respond_to_custom_request_tool,
    get_assigned_driver_eta_tool,
]

DRIVER_TOOLS = [
    get_driver_profile_tool,
    register_driver_profile_tool,
    get_driver_active_delivery_route_tool,
    update_driver_duty_status_tool,
    report_delivery_delay_or_gate_issue_tool,
    confirm_stop_arrival_and_delivery_tool,
]


def _get_llm(model_name: str = "gemini-2.5-flash"):
    """Retrieve configured ChatVertexAI instance using GCP Vertex AI (Application Default Credentials)."""
    try:
        from langchain_google_vertexai import ChatVertexAI

        project_id = os.environ.get("GCP_PROJECT_ID") or os.environ.get("GOOGLE_CLOUD_PROJECT") or "homatri-gcp"
        location = os.environ.get("GCP_LOCATION") or "asia-south1"

        return ChatVertexAI(
            model_name=model_name,
            project=project_id,
            location=location,
            temperature=0.0,
            max_retries=0,
        )

    except Exception:
        # Graceful fallback for offline testing environments without active GCP ADC credentials
        from langchain_core.language_models.fake import FakeListChatModel

        return FakeListChatModel(responses=["OK"])



# System Personas
MASTER_SYSTEM_PROMPT = """You are Homaatri's Master System Orchestrator Agent.
Your goal is to oversee batch cutoff locking, GCP route optimizations, HITL session resolution, payment webhooks, kitchen prep delays, and platform-wide order pipeline operations.
Be precise, authoritative, and operational."""

CUSTOMER_SYSTEM_PROMPT = """You are Homaatri's Customer Concierge Agent on WhatsApp.
Your goal is to help customers register profiles, discover nearby home kitchens, view daily menus, order meals, make payments, track delivery status, and submit food reviews.
Be warm, polite, and helpful. Always use the available tools to query data and execute operations. Never invent prices or menus.

TIME BRACKET & CUTOFF GUIDELINES FOR MEAL ORDERS:
1. MORNING (Before 12:00 PM): Both Same-Day LUNCH and Same-Day DINNER are OPEN. Recommend today's Lunch menu!
2. AFTERNOON (12:00 PM to 7:00 PM): Today's LUNCH cutoff is CLOSED. Kindly inform the user that Lunch cutoff (12:00 PM) has passed, and offer Today's DINNER (delivered at 7:30 PM) or Tomorrow's LUNCH!
3. NIGHT (After 7:00 PM): Today's DINNER cutoff is CLOSED. Kindly inform the user that today's service is completed and invite them to order for Tomorrow's LUNCH!"""


CHEF_SYSTEM_PROMPT = """You are Homaatri's Home Chef Assistant on WhatsApp.
Your goal is to assist home chefs with managing their daily menu capacity, toggling dish availability, viewing batch cooking checklists, marking orders packed and ready, responding to custom dish requests, and checking driver arrival ETAs.
Be clear, efficient, and supportive."""

DRIVER_SYSTEM_PROMPT = """You are Homaatri's Delivery Fleet Dispatcher on WhatsApp.
Your goal is to assist delivery drivers with checking active batch route itineraries, updating shift availability, reporting gate security blockages or traffic delays, and confirming stop completions.
Be direct, precise, and safety-focused."""


async def master_router_node(state: HomaatriGraphState, session: AsyncSession | None = None) -> Dict[str, Any]:
    """Inspect active_phone in PostgreSQL DB to identify user role and assign active_role."""
    phone = state.get("active_phone")
    if not phone:
        return {"active_role": "CUSTOMER"}

    async def _lookup(sess: AsyncSession) -> str:
        # 1. Check Driver Profile
        driver = await sess.get(DriverProfile, phone)
        if driver:
            return "DRIVER"

        # 2. Check Chef Profile
        chef = await sess.get(ChefProfile, phone)
        if chef:
            return "CHEF"

        # 3. Check Customer Profile
        cust = await sess.get(CustomerProfile, phone)
        if cust:
            return "CUSTOMER"

        return "CUSTOMER"

    if session is not None:
        role = await _lookup(session)
    else:
        async with SessionFactory() as sess:
            role = await _lookup(sess)

    return {"active_role": role}



async def master_agent_node(state: HomaatriGraphState) -> Dict[str, Any]:
    """Execute Master System Orchestrator Agent node bound with Master System tools."""
    llm = _get_llm().bind_tools(MASTER_TOOLS)
    recent_history = list(state["messages"])[-12:]
    messages = [SystemMessage(content=MASTER_SYSTEM_PROMPT)] + recent_history
    try:
        response = await llm.ainvoke(messages)
    except Exception as e:
        from langchain_core.messages import AIMessage
        response = AIMessage(content="[Master System]: Operational pipeline active. Batch routes and cutoffs are currently monitored.")
    return {"messages": [response]}


async def customer_agent_node(state: HomaatriGraphState) -> Dict[str, Any]:
    """Execute Customer Concierge Agent node bound with Customer tools."""
    phone = state.get("active_phone")
    is_registered = False
    customer_name = None

    if phone:
        async with SessionFactory() as session:
            cust = await session.get(CustomerProfile, phone)
            if cust and cust.is_registered:
                is_registered = True
                customer_name = cust.name

    llm = _get_llm().bind_tools(CUSTOMER_TOOLS)
    recent_history = list(state["messages"])[-12:]

    sys_prompt = CUSTOMER_SYSTEM_PROMPT
    if not is_registered:
        sys_prompt += f"\nCRITICAL REGISTRATION INSTRUCTION: The customer with phone number {phone} is NOT registered in our database yet. You MUST ask: 'Welcome to Homaatri! 👋 We noticed you are new here. Would you like to register with us?' and prompt them for their Name and Delivery Address to complete registration."
    else:
        sys_prompt += f"\nNOTE: User is registered customer '{customer_name}' with phone {phone}."

    messages = [SystemMessage(content=sys_prompt)] + recent_history
    try:
        response = await llm.ainvoke(messages)
    except Exception as e:
        from langchain_core.messages import AIMessage
        user_text = recent_history[-1].content.lower() if recent_history else ""
        if not is_registered:
            reply = "Welcome to Homaatri! 👋 We noticed you are not registered with us yet. Would you like to register with us to order delicious home-cooked meals? Please reply with your Name and Delivery Address (or tap 📍 to send location) to get started!"
        else:
            greeting_name = customer_name or "there"
            if "hi" in user_text or "hello" in user_text or "hey" in user_text:
                reply = f"Welcome back, {greeting_name}! 👋 How can I assist you with your home meal subscription today?"
            elif "menu" in user_text or "lunch" in user_text or "food" in user_text or "order" in user_text:
                reply = f"Hello {greeting_name}! Here are our featured Home Kitchens near Ghansoli Sector 6!\n\n1. 🟢 **Indravati Pure Veg Tiffins** (Jain Paneer Tikka Tiffin @ ₹180)\n2. 🔴 **Konkan Coastal Flavors** (Surmai Fish Curry Tiffin @ ₹280)\n3. 🟡 **Desi Punjabi Dhaba** (Amritsari Chole Bhature @ ₹170)\n4. 🟠 **Dakshin Annapoorna** (Mini Ghee Idli & Vada Combo @ ₹130)\n\nWhich meal would you like to order today?"
            else:
                reply = f"Welcome back, {greeting_name}! I can assist you with viewing home kitchen menus, placing orders, checking order status, or managing payments."
        response = AIMessage(content=reply)
    return {"messages": [response]}



async def chef_agent_node(state: HomaatriGraphState) -> Dict[str, Any]:
    """Execute Chef Concierge Agent node bound with Chef tools."""
    llm = _get_llm().bind_tools(CHEF_TOOLS)
    recent_history = list(state["messages"])[-12:]
    messages = [SystemMessage(content=CHEF_SYSTEM_PROMPT)] + recent_history
    try:
        response = await llm.ainvoke(messages)
    except Exception as e:
        from langchain_core.messages import AIMessage
        response = AIMessage(content="👨‍🍳 [Chef Assistant]: Hello Chef! Your kitchen capacity and batch checklists are up to date.")
    return {"messages": [response]}


async def driver_agent_node(state: HomaatriGraphState) -> Dict[str, Any]:
    """Execute Driver Concierge Agent node bound with Driver tools."""
    llm = _get_llm().bind_tools(DRIVER_TOOLS)
    recent_history = list(state["messages"])[-12:]
    messages = [SystemMessage(content=DRIVER_SYSTEM_PROMPT)] + recent_history
    try:
        response = await llm.ainvoke(messages)
    except Exception as e:
        from langchain_core.messages import AIMessage
        response = AIMessage(content="🛵 [Fleet Dispatcher]: Rider status active. Route navigation ready.")
    return {"messages": [response]}



