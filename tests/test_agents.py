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
    assert "get_order_status" in names
    assert "request_dietary_change" in names
    # customer must NOT own the gateway tools or the chef-side responder
    assert "mint_payment_link" not in names
    assert "respond_to_dietary_request" not in names


def test_master_agent_bindings():
    names = set(master_agent.tool_map)
    assert names == {"mint_payment_link", "process_payment_webhook", "relay_dietary_request"}


def test_chef_agent_bindings():
    names = set(chef_agent.tool_map)
    assert names == {"get_chef_profile", "get_chef_batch", "toggle_dish_stock",
                     "set_daily_capacity", "mark_order_ready", "respond_to_dietary_request"}


def test_driver_has_no_tools_yet():
    assert driver_agent.tool_map == {}
