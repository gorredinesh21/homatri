"""Homaatri agent tools (rebuilt flow-by-flow). See markdowns/tool_specs.md."""

from app.tools.chef_tools import (
    get_chef_batch,
    get_chef_profile,
    mark_order_ready,
    set_daily_capacity,
    toggle_dish_stock,
)
from app.tools.customer_tools import (
    add_item_to_order,
    create_order,
    find_nearby_kitchens,
    get_customer_profile,
    get_order_status,
    register_customer,
    request_payment,
    view_cart,
    view_chef_menu,
)
from app.tools.dietary import (
    relay_dietary_request,
    request_dietary_change,
    respond_to_dietary_request,
)
from app.tools.master_tools import mint_payment_link, process_payment_webhook

__all__ = [
    # Customer
    "get_customer_profile",
    "find_nearby_kitchens",
    "register_customer",
    "view_chef_menu",
    "create_order",
    "add_item_to_order",
    "view_cart",
    "request_payment",
    "get_order_status",
    # Chef
    "get_chef_profile",
    "get_chef_batch",
    "toggle_dish_stock",
    "set_daily_capacity",
    "mark_order_ready",
    # Dietary negotiation (Flow 6B)
    "request_dietary_change",
    "relay_dietary_request",
    "respond_to_dietary_request",
    # Master
    "mint_payment_link",
    "process_payment_webhook",
]
