"""Agent tool-binding tests — each agent holds only its own tools."""

from app.agents.agents import (
    chef_agent,
    customer_agent,
    driver_agent,
    master_agent,
)


def test_customer_agent_bindings():
    names = set(customer_agent.tool_map)
    assert "request_payment" in names
    assert "create_order" in names
    # customer must NOT own the gateway tools
    assert "mint_payment_link" not in names
    assert "process_payment_webhook" not in names


def test_master_agent_bindings():
    names = set(master_agent.tool_map)
    assert names == {"mint_payment_link", "process_payment_webhook"}


def test_chef_and_driver_have_no_tools_yet():
    assert chef_agent.tool_map == {}
    assert driver_agent.tool_map == {}
