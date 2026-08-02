"""Integration test suite for Milestone 3: LangGraph State Machine Architecture."""

import pytest
from decimal import Decimal
from langchain_core.messages import HumanMessage
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.graph import homatri_app
from app.agents.nodes import master_router_node
from app.models.chef import ChefProfile
from app.models.customer import CustomerProfile
from app.models.driver import DriverProfile


@pytest.mark.asyncio
async def test_master_router_node_customer_default(db_session: AsyncSession):
    session = db_session

    state = {
        "messages": [HumanMessage(content="Hello, I want to order food")],
        "active_phone": "9199999999",
        "active_role": "CUSTOMER",
    }
    res = await master_router_node(state)
    assert res["active_role"] == "CUSTOMER"


@pytest.mark.asyncio
async def test_master_router_node_chef(db_session: AsyncSession):
    session = db_session

    chef = ChefProfile(
        chef_phone="9876543210",
        kitchen_name="Indravati Tiffins",
        chef_name="Chef Sunita",
        address="Sector 4, Ghansoli",
        latitude=Decimal("19.1190086"),
        longitude=Decimal("72.9934054"),
    )
    session.add(chef)
    await session.flush()

    state = {
        "messages": [HumanMessage(content="What are my batch orders today?")],
        "active_phone": chef.chef_phone,
        "active_role": "CUSTOMER",
    }
    res = await master_router_node(state)
    assert res["active_role"] == "CHEF"


@pytest.mark.asyncio
async def test_master_router_node_driver(db_session: AsyncSession):
    session = db_session

    driver = DriverProfile(
        driver_phone="9111222333",
        driver_name="Vikram Driver",
        vehicle_type="BIKE",
        vehicle_number="MH43AB1234",
    )
    session.add(driver)
    await session.flush()

    state = {
        "messages": [HumanMessage(content="Show my active delivery route")],
        "active_phone": driver.driver_phone,
        "active_role": "CUSTOMER",
    }
    res = await master_router_node(state)
    assert res["active_role"] == "DRIVER"


@pytest.mark.asyncio
async def test_homatri_app_graph_compilation():
    # Verify StateGraph compiled application structure
    assert homatri_app is not None
    # Verify graph nodes exist
    nodes = homatri_app.get_graph().nodes
    assert "master_router_node" in nodes
    assert "master_agent_node" in nodes
    assert "customer_agent_node" in nodes
    assert "chef_agent_node" in nodes
    assert "driver_agent_node" in nodes
    assert "tools" in nodes
