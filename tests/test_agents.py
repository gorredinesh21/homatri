"""Single customer agent tool-binding tests — one agent owns the whole conversation."""

from app.agents.agents import customer_agent


def test_customer_agent_bindings():
    names = set(customer_agent.tool_map)
    for t in ("get_customer_profile", "register_customer", "find_nearby_kitchens", "view_chef_menu",
              "create_order", "add_item_to_order", "add_special_instructions", "view_cart",
              "request_payment", "check_my_payment", "get_order_status", "cancel_order",
              "submit_order_review", "escalate_to_admin"):
        assert t in names, f"missing tool: {t}"
    # The gateway minting tool and the chef-side responders must NOT be agent-facing anymore
    assert "mint_payment_link" not in names
    assert "respond_to_dietary_request" not in names
    assert "respond_to_cancellation" not in names
    # Multi-agent relays are retired
    assert "request_dietary_change" not in names
    assert "request_order_topup" not in names


def test_escalate_bound_to_agent():
    assert "escalate_to_admin" in set(customer_agent.tool_map)


def test_single_agent_no_multi_persona():
    import app.agents as pkg
    for legacy in ("chef_agent", "driver_agent", "master_agent", "AGENTS"):
        assert not hasattr(pkg, legacy), f"legacy persona {legacy} still exported"
