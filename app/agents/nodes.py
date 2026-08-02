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
Be warm, polite, and helpful. Always use the available tools to query data and execute operations. Never invent prices or menus."""

CHEF_SYSTEM_PROMPT = """You are Homaatri's Home Chef Assistant on WhatsApp.
Your goal is to assist home chefs with managing their daily menu capacity, toggling dish availability, viewing batch cooking checklists, marking orders packed and ready, responding to custom dish requests, and checking driver arrival ETAs.
Be clear, efficient, and supportive."""

DRIVER_SYSTEM_PROMPT = """You are Homaatri's Delivery Fleet Dispatcher on WhatsApp.
Your goal is to assist delivery drivers with checking active batch route itineraries, updating shift availability, reporting gate security blockages or traffic delays, and confirming stop completions.
Be direct, precise, and safety-focused."""


async def master_router_node(state: HomaatriGraphState) -> Dict[str, Any]:
    """Inspect active_phone in PostgreSQL DB to identify user role and assign active_role."""
    phone = state.get("active_phone")
    if not phone:
        return {"active_role": "CUSTOMER"}

    async with SessionFactory() as session:
        # 1. Check Driver Profile
        driver = await session.get(DriverProfile, phone)
        if driver:
            return {"active_role": "DRIVER"}

        # 2. Check Chef Profile
        chef = await session.get(ChefProfile, phone)
        if chef:
            return {"active_role": "CHEF"}

        # 3. Check Customer Profile
        cust = await session.get(CustomerProfile, phone)
        if cust:
            return {"active_role": "CUSTOMER"}

    # Default to CUSTOMER role for onboarding
    return {"active_role": "CUSTOMER"}


async def master_agent_node(state: HomaatriGraphState) -> Dict[str, Any]:
    """Execute Master System Orchestrator Agent node bound with Master System tools."""
    llm = _get_llm().bind_tools(MASTER_TOOLS)
    messages = [SystemMessage(content=MASTER_SYSTEM_PROMPT)] + list(state["messages"])
    response = await llm.ainvoke(messages)
    return {"messages": [response]}


async def customer_agent_node(state: HomaatriGraphState) -> Dict[str, Any]:
    """Execute Customer Concierge Agent node bound with Customer tools."""
    llm = _get_llm().bind_tools(CUSTOMER_TOOLS)
    messages = [SystemMessage(content=CUSTOMER_SYSTEM_PROMPT)] + list(state["messages"])
    response = await llm.ainvoke(messages)
    return {"messages": [response]}


async def chef_agent_node(state: HomaatriGraphState) -> Dict[str, Any]:
    """Execute Chef Concierge Agent node bound with Chef tools."""
    llm = _get_llm().bind_tools(CHEF_TOOLS)
    messages = [SystemMessage(content=CHEF_SYSTEM_PROMPT)] + list(state["messages"])
    response = await llm.ainvoke(messages)
    return {"messages": [response]}


async def driver_agent_node(state: HomaatriGraphState) -> Dict[str, Any]:
    """Execute Driver Concierge Agent node bound with Driver tools."""
    llm = _get_llm().bind_tools(DRIVER_TOOLS)
    messages = [SystemMessage(content=DRIVER_SYSTEM_PROMPT)] + list(state["messages"])
    response = await llm.ainvoke(messages)
    return {"messages": [response]}

