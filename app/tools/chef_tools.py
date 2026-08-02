"""Chef Domain LLM Tools (Category 1).

Encapsulates Chef Agent tools with Guard 2 Pre-Condition Assertions.
Tool 1: get_chef_profile_tool (Read-only, Same Domain).
Tool 2: get_chef_menu_tool (Read-only, Same Domain).
Tool 3: update_daily_dish_capacity_tool (Write Executor #1, Same Domain).
Tool 4: toggle_dish_availability_tool (Write Executor #2, Same Domain).
Tool 5: get_chef_daily_batch_checklist_tool (Read-only, Same Domain).
Tool 6: mark_orders_packed_ready_tool (Write Executor #3, Same Domain).
Tool 7: get_chef_earnings_summary_tool (Read-only, Same Domain).
"""

from __future__ import annotations

from datetime import date, datetime

from decimal import Decimal
from typing import Any, Optional

from langchain_core.tools import tool
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.executors.chef import execute_daily_capacity_upsert, execute_dish_stock_toggle, execute_order_readiness_record
from app.executors.master import execute_conversation_message_insert, execute_outbound_whatsapp_enqueue
from app.models.chef import ChefDailyInventory, ChefMenuItem, ChefOrderReadiness, ChefProfile
from app.models.customer import CustomerOrder, CustomerOrderItem



# =============================================================================
# TOOL 1: get_chef_profile_tool
# =============================================================================
class GetChefProfileInput(BaseModel):
    chef_phone: str = Field(
        ...,
        description="Normalized 10-digit phone number of the home kitchen (e.g. '9876543210')",
    )


async def get_chef_profile(
    session: AsyncSession,
    chef_phone: str,
) -> dict[str, Any]:
    """Query database for chef's profile details with Guard 2 Pre-Condition Assertions."""
    assert chef_phone and len(chef_phone) >= 10, f"Invalid chef phone number: {chef_phone}"

    chef = await session.get(ChefProfile, chef_phone)
    assert chef is not None, f"Kitchen profile not found for phone: {chef_phone}"

    return {
        "chef_phone": chef.chef_phone,
        "kitchen_name": chef.kitchen_name,
        "chef_name": chef.chef_name,
        "address": chef.address,
        "apartment_or_locality": chef.apartment_or_locality,
        "city": chef.city,
        "pincode": chef.pincode,
        "latitude": float(chef.latitude),
        "longitude": float(chef.longitude),
        "fssai_license_number": chef.fssai_license_number,
        "dietary_type": chef.dietary_type,
        "active_status": chef.active_status,
        "is_verified": chef.is_verified,
    }


@tool("get_chef_profile_tool", args_schema=GetChefProfileInput)
async def get_chef_profile_tool(chef_phone: str) -> str:
    """Retrieve identity, kitchen name, address, GPS coordinates, and operating status for a home chef."""
    from app.db.session import SessionFactory

    async with SessionFactory() as session:
        data = await get_chef_profile(session, chef_phone=chef_phone)
        status_str = "ACTIVE" if data["active_status"] else "INACTIVE"
        return (
            f"Kitchen Profile for {data['kitchen_name']} ({data['chef_phone']}):\n"
            f"Chef Name: {data['chef_name']}\n"
            f"Address: {data['address']}, {data['city']} (Locality: {data['apartment_or_locality'] or 'N/A'})\n"
            f"Coordinates: Lat {data['latitude']}, Lng {data['longitude']}\n"
            f"FSSAI License: {data['fssai_license_number'] or 'N/A'}\n"
            f"Dietary Type: {data['dietary_type'] or 'ANY'}\n"
            f"Status: {status_str} (Verified: {data['is_verified']})"
        )


# =============================================================================
# TOOL 2: get_chef_menu_tool
# =============================================================================
class GetChefMenuInput(BaseModel):
    chef_phone: str = Field(
        ...,
        description="Normalized 10-digit phone number of the home kitchen (e.g. '9876543210')",
    )
    include_unavailable: bool = Field(
        default=True,
        description="If True, includes dishes currently marked out of stock. If False, returns in-stock dishes only.",
    )


async def get_chef_menu(
    session: AsyncSession,
    chef_phone: str,
    include_unavailable: bool = True,
) -> list[dict[str, Any]]:
    """Query database for chef's menu items with Guard 2 Pre-Condition Assertions."""
    assert chef_phone and len(chef_phone) >= 10, f"Invalid chef phone number: {chef_phone}"

    chef = await session.get(ChefProfile, chef_phone)
    assert chef is not None, f"Kitchen profile not found for phone: {chef_phone}"

    stmt = select(ChefMenuItem).where(ChefMenuItem.chef_phone == chef_phone)
    if not include_unavailable:
        stmt = stmt.where(ChefMenuItem.is_available.is_(True))

    stmt = stmt.order_by(ChefMenuItem.dish_name)
    result = await session.execute(stmt)
    items = result.scalars().all()

    return [
        {
            "menu_item_id": item.menu_item_id,
            "dish_name": item.dish_name,
            "description": item.description,
            "unit_price": float(item.unit_price),
            "meal_type": item.meal_type,
            "dietary_tag": item.dietary_tag,
            "spice_level": item.spice_level,
            "max_availability": item.max_availability,
            "is_available": item.is_available,
        }
        for item in items
    ]


@tool("get_chef_menu_tool", args_schema=GetChefMenuInput)
async def get_chef_menu_tool(chef_phone: str, include_unavailable: bool = True) -> str:
    """Retrieve the menu offerings for a home kitchen by chef phone number.

    Returns dish names, prices, dietary tags, spice levels, and stock availability.
    """
    from app.db.session import SessionFactory

    async with SessionFactory() as session:
        items = await get_chef_menu(session, chef_phone=chef_phone, include_unavailable=include_unavailable)
        if not items:
            return f"No menu items found for kitchen {chef_phone}."
        return f"Menu for kitchen ({chef_phone}):\n" + "\n".join(
            f"- [{item['menu_item_id']}] {item['dish_name']} — ₹{item['unit_price']:.2f} "
            f"({item['dietary_tag']}, {item['meal_type']}) [{'IN STOCK' if item['is_available'] else 'OUT OF STOCK'}]"
            for item in items
        )


# =============================================================================
# TOOL 3: update_daily_dish_capacity_tool
# =============================================================================
class UpdateDailyDishCapacityInput(BaseModel):
    chef_phone: str = Field(
        ...,
        description="Normalized 10-digit phone number of the chef (e.g. '9876543210')",
    )
    menu_item_id: str = Field(
        ...,
        description="Prefixed dish ID (e.g. 'itm_paneer01')",
    )
    service_date: str = Field(
        ...,
        description="Service date in ISO format YYYY-MM-DD (e.g. '2026-08-01')",
    )
    meal_window: str = Field(
        ...,
        description="Meal window: 'LUNCH' or 'DINNER'",
    )
    max_capacity: int = Field(
        ...,
        description="Maximum portion limit chef can cook for this window (e.g. 15)",
    )
    is_unlimited: bool = Field(
        default=False,
        description="Set True if dish has no prep limit.",
    )
    notes: Optional[str] = Field(
        default=None,
        description="Optional operational note (e.g. 'Limited paneer availability')",
    )


async def update_daily_dish_capacity(
    session: AsyncSession,
    *,
    chef_phone: str,
    menu_item_id: str,
    service_date: str,
    meal_window: str,
    max_capacity: int,
    is_unlimited: bool = False,
    notes: str | None = None,
) -> ChefDailyInventory:
    """Set or override dish daily capacity with Guard 2 Pre-Condition Assertions."""
    assert max_capacity >= 0, f"Capacity cannot be negative, got {max_capacity}"
    assert meal_window in {"LUNCH", "DINNER"}, f"Invalid meal window: '{meal_window}'. Must be LUNCH or DINNER"

    item = await session.get(ChefMenuItem, menu_item_id)
    assert item is not None, f"Menu item not found: {menu_item_id}"
    assert item.chef_phone == chef_phone, (
        f"Dish {menu_item_id} ('{item.dish_name}') belongs to chef {item.chef_phone}, "
        f"not chef {chef_phone}."
    )

    date_obj = date.fromisoformat(service_date)

    inventory = await execute_daily_capacity_upsert(
        session,
        chef_phone=chef_phone,
        menu_item_id=menu_item_id,
        service_date=date_obj,
        meal_window=meal_window,
        max_capacity=max_capacity,
        is_unlimited=is_unlimited,
        notes=notes,
    )
    return inventory


@tool("update_daily_dish_capacity_tool", args_schema=UpdateDailyDishCapacityInput)
async def update_daily_dish_capacity_tool(
    chef_phone: str,
    menu_item_id: str,
    service_date: str,
    meal_window: str,
    max_capacity: int,
    is_unlimited: bool = False,
    notes: Optional[str] = None,
) -> str:
    """Set or update the daily portion preparation capacity for a dish on a specific date and meal window."""
    from app.db.session import transaction

    async with transaction() as session:
        inv = await update_daily_dish_capacity(
            session,
            chef_phone=chef_phone,
            menu_item_id=menu_item_id,
            service_date=service_date,
            meal_window=meal_window,
            max_capacity=max_capacity,
            is_unlimited=is_unlimited,
            notes=notes,
        )
        return (
            f"Successfully updated capacity for dish '{menu_item_id}' on {service_date} ({meal_window}) "
            f"to {inv.max_capacity} portions."
        )


# =============================================================================
# TOOL 4: toggle_dish_availability_tool
# =============================================================================
class ToggleDishAvailabilityInput(BaseModel):
    chef_phone: str = Field(
        ...,
        description="Normalized 10-digit phone number of the chef (e.g. '9876543210')",
    )
    menu_item_id: str = Field(
        ...,
        description="Prefixed dish ID (e.g. 'itm_paneer01')",
    )
    is_available: bool = Field(
        ...,
        description="True to mark dish IN STOCK / AVAILABLE; False to mark OUT OF STOCK",
    )


async def toggle_dish_availability(
    session: AsyncSession,
    *,
    chef_phone: str,
    menu_item_id: str,
    is_available: bool,
) -> ChefMenuItem:
    """Toggle dish availability status with Guard 2 Pre-Condition Assertions."""
    item = await session.get(ChefMenuItem, menu_item_id)
    assert item is not None, f"Menu item not found: {menu_item_id}"
    assert item.chef_phone == chef_phone, (
        f"Dish {menu_item_id} ('{item.dish_name}') belongs to chef {item.chef_phone}, "
        f"not chef {chef_phone}."
    )

    updated_item = await execute_dish_stock_toggle(
        session,
        menu_item_id=menu_item_id,
        is_available=is_available,
    )
    return updated_item


@tool("toggle_dish_availability_tool", args_schema=ToggleDishAvailabilityInput)
async def toggle_dish_availability_tool(
    chef_phone: str,
    menu_item_id: str,
    is_available: bool,
) -> str:
    """Instantly toggle a dish IN or OUT of stock for ordering."""
    from app.db.session import transaction

    async with transaction() as session:
        item = await toggle_dish_availability(
            session,
            chef_phone=chef_phone,
            menu_item_id=menu_item_id,
            is_available=is_available,
        )
        status_str = "IN STOCK" if item.is_available else "OUT OF STOCK"
        return f"Successfully updated dish '{item.dish_name}' ({menu_item_id}) status to {status_str}."


# =============================================================================
# TOOL 5: get_chef_daily_batch_checklist_tool
# =============================================================================
class GetChefDailyBatchChecklistInput(BaseModel):
    chef_phone: str = Field(
        ...,
        description="Normalized 10-digit phone number of the chef (e.g. '9876543210')",
    )
    service_date: str = Field(
        ...,
        description="Service date in ISO format YYYY-MM-DD (e.g. '2026-07-31')",
    )
    meal_window: str = Field(
        ...,
        description="Meal window: 'LUNCH' or 'DINNER'",
    )


async def get_chef_daily_batch_checklist(
    session: AsyncSession,
    *,
    chef_phone: str,
    service_date: str,
    meal_window: str,
) -> dict[str, Any]:
    """Retrieve daily batch cooking checklist for a chef with Guard 2 Pre-Condition Assertions."""
    assert meal_window in {"LUNCH", "DINNER"}, f"Invalid meal window: '{meal_window}'. Must be LUNCH or DINNER"
    chef = await session.get(ChefProfile, chef_phone)
    assert chef is not None, f"Kitchen profile not found for phone: {chef_phone}"

    date_obj = date.fromisoformat(service_date)

    confirmed_statuses = {"CONFIRMED", "BATCHED", "COOKING", "PACKED", "PICKED_UP", "DELIVERED"}
    orders_stmt = select(CustomerOrder).where(
        CustomerOrder.chef_phone == chef_phone,
        CustomerOrder.service_date == date_obj,
        CustomerOrder.meal_window == meal_window,
        CustomerOrder.status.in_(confirmed_statuses),
    )
    orders = (await session.execute(orders_stmt)).scalars().all()
    order_ids = [o.order_id for o in orders]

    if not order_ids:
        return {
            "kitchen_name": chef.kitchen_name,
            "service_date": service_date,
            "meal_window": meal_window,
            "total_orders": 0,
            "portions_to_cook": {},
            "packed_ready_count": 0,
        }

    items_stmt = (
        select(
            CustomerOrderItem.dish_name,
            func.sum(CustomerOrderItem.quantity).label("total_quantity"),
        )
        .where(CustomerOrderItem.order_id.in_(order_ids))
        .group_by(CustomerOrderItem.dish_name)
        .order_by(CustomerOrderItem.dish_name)
    )
    items_summary = (await session.execute(items_stmt)).all()
    portions_to_cook = {dish_name: int(qty) for dish_name, qty in items_summary}

    ready_count_stmt = select(func.count()).select_from(ChefOrderReadiness).where(
        ChefOrderReadiness.order_id.in_(order_ids)
    )
    packed_ready_count = (await session.execute(ready_count_stmt)).scalar_one()

    return {
        "kitchen_name": chef.kitchen_name,
        "service_date": service_date,
        "meal_window": meal_window,
        "total_orders": len(orders),
        "portions_to_cook": portions_to_cook,
        "packed_ready_count": packed_ready_count,
    }


@tool("get_chef_daily_batch_checklist_tool", args_schema=GetChefDailyBatchChecklistInput)
async def get_chef_daily_batch_checklist_tool(
    chef_phone: str,
    service_date: str,
    meal_window: str,
) -> str:
    """Retrieve the batch preparation checklist showing total dish portion counts needed for a meal window."""
    from app.db.session import SessionFactory

    async with SessionFactory() as session:
        data = await get_chef_daily_batch_checklist(
            session,
            chef_phone=chef_phone,
            service_date=service_date,
            meal_window=meal_window,
        )
        if data["total_orders"] == 0:
            return (
                f"Batch Checklist for {data['kitchen_name']} — {service_date} ({meal_window}):\n"
                f"No active orders found for this meal window."
            )

        portions_list = "\n".join(
            f"- {dish_name}: {qty} portions"
            for dish_name, qty in data["portions_to_cook"].items()
        )
        return (
            f"Batch Cooking Checklist for {data['kitchen_name']} — {service_date} ({meal_window}):\n"
            f"Total Confirmed Orders: {data['total_orders']}\n"
            f"Portions to Prepare:\n{portions_list}\n"
            f"Packaging Status: {data['packed_ready_count']} / {data['total_orders']} marked PACKED_READY."
        )


# =============================================================================
# TOOL 6: mark_orders_packed_ready_tool
# =============================================================================
class MarkOrdersPackedReadyInput(BaseModel):
    chef_phone: str = Field(
        ...,
        description="Normalized 10-digit phone number of the chef (e.g. '9876543210')",
    )
    order_id: str = Field(
        ...,
        description="Prefixed order ID (e.g. 'ord_123456')",
    )
    box_count: Optional[int] = Field(
        default=None,
        description="Optional number of container boxes packed (if specified by chef)",
    )
    special_packing_notes: Optional[str] = Field(
        default=None,
        description="Optional chef packing note (e.g. 'Sealed curry container packed separately')",
    )


async def mark_orders_packed_ready(
    session: AsyncSession,
    *,
    chef_phone: str,
    order_id: str,
    box_count: int | None = None,
    special_packing_notes: str | None = None,
) -> ChefOrderReadiness:
    """Record order as PACKED_READY with Guard 2 Pre-Condition Assertions."""
    if box_count is not None:
        assert box_count >= 1, f"Box count must be at least 1, got {box_count}"

    order = await session.get(CustomerOrder, order_id)
    assert order is not None, f"Order not found: {order_id}"
    assert order.chef_phone == chef_phone, (
        f"Order {order_id} belongs to chef {order.chef_phone}, not chef {chef_phone}."
    )
    valid_statuses = {"CONFIRMED", "BATCHED", "COOKING", "PACKED"}
    assert order.status in valid_statuses, (
        f"Cannot mark order {order_id} as packed ready; current status is '{order.status}'. "
        f"Order must be in one of {valid_statuses}."
    )

    readiness = await execute_order_readiness_record(
        session,
        order_id=order_id,
        chef_phone=chef_phone,
        box_count=box_count,
        special_packing_notes=special_packing_notes,
    )
    return readiness


@tool("mark_orders_packed_ready_tool", args_schema=MarkOrdersPackedReadyInput)
async def mark_orders_packed_ready_tool(
    chef_phone: str,
    order_id: str,
    box_count: Optional[int] = None,
    special_packing_notes: Optional[str] = None,
) -> str:
    """Record that an order is packed in boxes and ready for driver pickup."""
    from app.db.session import transaction

    async with transaction() as session:
        readiness = await mark_orders_packed_ready(
            session,
            chef_phone=chef_phone,
            order_id=order_id,
            box_count=box_count,
            special_packing_notes=special_packing_notes,
        )
        box_str = f" ({readiness.box_count} boxes)" if readiness.box_count is not None else ""
        return f"Order '{order_id}' successfully recorded as PACKED_READY{box_str}."


# =============================================================================
# TOOL 7: get_chef_earnings_summary_tool
# =============================================================================
class GetChefEarningsSummaryInput(BaseModel):
    chef_phone: str = Field(
        ...,
        description="Normalized 10-digit phone number of the chef (e.g. '9876543210')",
    )
    start_date: str = Field(
        ...,
        description="Start date in ISO format YYYY-MM-DD (e.g. '2026-07-01')",
    )
    end_date: str = Field(
        ...,
        description="End date in ISO format YYYY-MM-DD (e.g. '2026-07-31')",
    )


async def get_chef_earnings_summary(
    session: AsyncSession,
    *,
    chef_phone: str,
    start_date: str,
    end_date: str,
) -> dict[str, Any]:
    """Calculate kitchen revenue and order statistics with Guard 2 Pre-Condition Assertions."""
    start_obj = date.fromisoformat(start_date)
    end_obj = date.fromisoformat(end_date)
    assert start_obj <= end_obj, f"Start date ({start_date}) cannot be after end date ({end_date})"

    chef = await session.get(ChefProfile, chef_phone)
    assert chef is not None, f"Kitchen profile not found for phone: {chef_phone}"

    active_statuses = {"CONFIRMED", "BATCHED", "COOKING", "PACKED", "PICKED_UP", "DELIVERED"}
    stmt = select(CustomerOrder).where(
        CustomerOrder.chef_phone == chef_phone,
        CustomerOrder.service_date >= start_obj,
        CustomerOrder.service_date <= end_obj,
        CustomerOrder.status.in_(active_statuses),
    )
    orders = (await session.execute(stmt)).scalars().all()

    total_orders = len(orders)
    total_revenue = sum(o.cart_subtotal for o in orders)
    delivered_orders = sum(1 for o in orders if o.status == "DELIVERED")

    return {
        "kitchen_name": chef.kitchen_name,
        "start_date": start_date,
        "end_date": end_date,
        "total_orders": total_orders,
        "delivered_orders": delivered_orders,
        "total_revenue": float(total_revenue),
        "average_order_value": float(total_revenue / total_orders) if total_orders > 0 else 0.0,
    }


@tool("get_chef_earnings_summary_tool", args_schema=GetChefEarningsSummaryInput)
async def get_chef_earnings_summary_tool(
    chef_phone: str,
    start_date: str,
    end_date: str,
) -> str:
    """Calculate kitchen earnings, gross food revenue, and fulfilled order count over a date range."""
    from app.db.session import SessionFactory

    async with SessionFactory() as session:
        data = await get_chef_earnings_summary(
            session,
            chef_phone=chef_phone,
            start_date=start_date,
            end_date=end_date,
        )
        return (
            f"Earnings Summary for {data['kitchen_name']} ({start_date} to {end_date}):\n"
            f"Total Active/Fulfilled Orders: {data['total_orders']}\n"
            f"Delivered Orders: {data['delivered_orders']}\n"
            f"Kitchen Gross Earnings: ₹{data['total_revenue']:.2f}\n"
            f"Average Order Value: ₹{data['average_order_value']:.2f}"
        )


# =============================================================================
# TOOL 8: relay_order_ready_to_driver_tool
# =============================================================================
class RelayOrderReadyToDriverInput(BaseModel):
    chef_phone: str = Field(
        ...,
        description="Normalized 10-digit phone number of home chef (e.g. '9876543210')",
    )
    order_id: str = Field(
        ...,
        description="Target CustomerOrder ID (e.g. 'ord_123456')",
    )
    packed_containers_count: int = Field(
        ...,
        description="Total count of packed tiffin containers (e.g. 3)",
    )
    special_notes: Optional[str] = Field(
        default=None,
        description="Handover instructions for driver (e.g. 'Hot rasam packed separately in thermal pouch')",
    )


async def relay_order_ready_to_driver(
    session: AsyncSession,
    *,
    chef_phone: str,
    order_id: str,
    packed_containers_count: int,
    special_notes: str | None = None,
) -> dict[str, Any]:
    """Transition order status to PACKED via DW1, locate assigned driver, and enqueue WhatsApp alerts to Driver & Customer."""
    assert chef_phone and len(chef_phone) >= 10, f"Invalid chef_phone: {chef_phone}"
    assert order_id, "order_id cannot be empty"
    assert packed_containers_count >= 1, f"packed_containers_count must be at least 1, got {packed_containers_count}"

    order = await session.get(CustomerOrder, order_id)
    assert order is not None, f"Order not found: {order_id}"
    assert order.chef_phone == chef_phone, f"Order {order_id} belongs to chef {order.chef_phone}, not chef {chef_phone}"
    assert order.status in {"CONFIRMED", "BATCHED", "COOKING"}, f"Cannot mark order ready in status '{order.status}'"

    # 1. Transition Order Status to PACKED via Delegated Executor DW1
    from app.executors.customer import execute_order_status_transition

    await execute_order_status_transition(
        session,
        order_id=order_id,
        target_status="PACKED",
        actor_role="CHEF",
        reason=f"Chef marked ready with {packed_containers_count} packed containers",
    )

    # 2. Locate Assigned Driver on Delivery Route via SystemDeliveryStopOrder
    from app.models.system import SystemDeliveryRoute, SystemDeliveryStop, SystemDeliveryStopOrder

    stmt_stop_order = select(SystemDeliveryStopOrder.stop_id).where(
        SystemDeliveryStopOrder.order_id == order_id
    )
    stop_id = (await session.execute(stmt_stop_order)).scalar_one_or_none()

    driver_phone = None
    driver_notified = False

    if stop_id:
        stop = await session.get(SystemDeliveryStop, stop_id)
        if stop and stop.route_id:
            route = await session.get(SystemDeliveryRoute, stop.route_id)
            if route and route.driver_phone:
                driver_phone = route.driver_phone


    # 3. Enqueue Outbound WhatsApp Alert to Driver (if assigned)
    notes_str = f" Handover note: \"{special_notes.strip()}\"." if special_notes else ""
    if driver_phone:
        driver_msg = (
            f"🍱 ORDER PACKED & READY FOR PICKUP!\n"
            f"Kitchen: {order.kitchen_name} ({chef_phone})\n"
            f"Order #{order_id} ({packed_containers_count} containers).{notes_str}\n"
            f"Please proceed for pickup."
        )
        await execute_outbound_whatsapp_enqueue(
            session,
            recipient_phone=driver_phone,
            recipient_role="DRIVER",
            message_text=driver_msg,
        )
        await execute_conversation_message_insert(
            session,
            phone=driver_phone,
            actor_role="DRIVER",
            direction="OUTBOUND",
            source="SYSTEM_ALERT",
            message_text=driver_msg,
        )
        driver_notified = True

    # 4. Enqueue Outbound WhatsApp Notice to Customer
    cust_msg = (
        f"👩‍🍳 YOUR MEAL IS FRESHLY PACKED!\n"
        f"Chef ({order.kitchen_name}) has packed your order ({packed_containers_count} containers).\n"
        f"Order #{order_id} is ready and your delivery driver is on the way!"
    )
    await execute_outbound_whatsapp_enqueue(
        session,
        recipient_phone=order.customer_phone,
        recipient_role="CUSTOMER",
        message_text=cust_msg,
    )
    await execute_conversation_message_insert(
        session,
        phone=order.customer_phone,
        actor_role="CUSTOMER",
        direction="OUTBOUND",
        source="SYSTEM_ALERT",
        message_text=cust_msg,
    )

    # 5. Record Audit Event via Master Executor #8
    from app.executors.master import execute_system_audit_log

    await execute_system_audit_log(
        session,
        event_type="ORDER_PACKED_READY",
        source_role="CHEF",
        target_role="DRIVER" if driver_phone else "SYSTEM",
        payload={
            "order_id": order_id,
            "chef_phone": chef_phone,
            "packed_containers_count": packed_containers_count,
            "driver_phone": driver_phone,
            "driver_notified": driver_notified,
        },
        severity="INFO",
    )

    return {
        "order_id": order_id,
        "status": "PACKED",
        "chef_phone": chef_phone,
        "packed_containers_count": packed_containers_count,
        "driver_notified": driver_notified,
        "driver_phone": driver_phone,
    }


@tool("relay_order_ready_to_driver_tool", args_schema=RelayOrderReadyToDriverInput)
async def relay_order_ready_to_driver_tool(
    chef_phone: str,
    order_id: str,
    packed_containers_count: int,
    special_notes: Optional[str] = None,
) -> str:
    """Relay food readiness status from home chef to assigned delivery driver and customer via WhatsApp."""
    from app.db.session import transaction

    async with transaction() as session:
        res = await relay_order_ready_to_driver(
            session,
            chef_phone=chef_phone,
            order_id=order_id,
            packed_containers_count=packed_containers_count,
            special_notes=special_notes,
        )
        driver_str = f"Driver ({res['driver_phone']}) notified via WhatsApp." if res['driver_notified'] else "Driver assignment pending."
        return (
            f"✅ Order #{res['order_id']} Marked PACKED & READY!\n"
            f"Kitchen: {res['chef_phone']} | Containers: {res['packed_containers_count']}\n"
            f"{driver_str}\n"
            f"Customer notification dispatched."
        )


# =============================================================================
# TOOL 9: respond_to_custom_request_tool
# =============================================================================
class RespondToCustomRequestInput(BaseModel):
    hitl_session_id: str = Field(
        ...,
        description="ID of the active HITL custom dish inquiry session (e.g. 'hitl_12345')",
    )
    chef_phone: str = Field(
        ...,
        description="Normalized 10-digit phone number of home chef (e.g. '9876543210')",
    )
    decision: str = Field(
        ...,
        description="3-way decision enum: 'ACCEPTED', 'DECLINED', or 'COUNTER_OFFER'",
    )
    custom_dish_name: Optional[str] = Field(
        default=None,
        description="Name of accepted custom dish or counter-offered alternative dish",
    )
    custom_dish_price: Optional[Decimal] = Field(
        default=None,
        description="Price per portion in INR (required if ACCEPTED or COUNTER_OFFER)",
    )
    chef_message: Optional[str] = Field(
        default=None,
        description="Personal message from chef to customer explaining decision or counter-offer",
    )


async def respond_to_custom_request(
    session: AsyncSession,
    *,
    hitl_session_id: str,
    chef_phone: str,
    decision: str,
    custom_dish_name: str | None = None,
    custom_dish_price: Decimal | None = None,
    chef_message: str | None = None,
) -> dict[str, Any]:
    """Resolve HITL custom dish request session with 3-way decision (ACCEPTED/DECLINED/COUNTER_OFFER) and notify customer on WhatsApp."""
    assert hitl_session_id, "hitl_session_id cannot be empty"
    assert chef_phone and len(chef_phone) >= 10, f"Invalid chef_phone: {chef_phone}"
    assert decision in {"ACCEPTED", "DECLINED", "COUNTER_OFFER"}, f"Invalid decision: '{decision}'. Must be ACCEPTED, DECLINED, or COUNTER_OFFER"

    if decision in {"ACCEPTED", "COUNTER_OFFER"}:
        assert custom_dish_price is not None and custom_dish_price > Decimal("0.00"), "custom_dish_price must be > 0.00 when decision is ACCEPTED or COUNTER_OFFER"

    from app.models.system import SystemHitlSession
    hitl = await session.get(SystemHitlSession, hitl_session_id)
    assert hitl is not None, f"HITL session not found: {hitl_session_id}"
    assert hitl.status == "WAITING", f"HITL session {hitl_session_id} is already in status '{hitl.status}'"

    customer_phone = hitl.payload.get("customer_phone") or hitl.waiting_on_phone
    assert customer_phone, "Customer phone number missing in HITL session payload"


    # 1. Update HITL Session Status to RESOLVED
    hitl.status = "RESOLVED"
    hitl.resolved_at = datetime.now()

    # 2. Build Customer WhatsApp Notification
    chef = await session.get(ChefProfile, chef_phone)
    kitchen_name = chef.kitchen_name if chef else "Home Kitchen"
    message_str = f" Chef note: \"{chef_message.strip()}\"." if chef_message else ""

    if decision == "ACCEPTED":
        cust_msg = (
            f"🎉 CUSTOM DISH ACCEPTED BY CHEF ({kitchen_name})!\n"
            f"Dish: {custom_dish_name or 'Custom Special'}\n"
            f"Price per portion: ₹{custom_dish_price:.2f}.{message_str}\n"
            f"You can now proceed to add this to your order!"
        )
    elif decision == "COUNTER_OFFER":
        cust_msg = (
            f"💡 CHEF COUNTER-OFFER ({kitchen_name}):\n"
            f"Alternative Dish: {custom_dish_name or 'Special Alternative'}\n"
            f"Price per portion: ₹{custom_dish_price:.2f}.{message_str}\n"
            f"Reply YES to accept or NO to decline."
        )
    else:  # DECLINED
        cust_msg = (
            f"❌ CUSTOM REQUEST DECLINED ({kitchen_name}):\n"
            f"Chef Sunita is unable to prepare the requested custom dish at this time.{message_str}\n"
            f"Please feel free to choose from the regular daily menu!"
        )

    # 3. Enqueue WhatsApp message to Customer
    await execute_outbound_whatsapp_enqueue(
        session,
        recipient_phone=customer_phone,
        recipient_role="CUSTOMER",
        message_text=cust_msg,
    )
    await execute_conversation_message_insert(
        session,
        phone=customer_phone,
        actor_role="CUSTOMER",
        direction="OUTBOUND",
        source="SYSTEM_ALERT",
        message_text=cust_msg,
    )

    # 4. Record Audit Event via Master Executor #8
    from app.executors.master import execute_system_audit_log
    await execute_system_audit_log(
        session,
        event_type="CHEF_CUSTOM_REQUEST_RESOLVED",
        source_role="CHEF",
        target_role="CUSTOMER",
        payload={
            "session_id": hitl_session_id,
            "chef_phone": chef_phone,
            "customer_phone": customer_phone,
            "decision": decision,
            "custom_dish_name": custom_dish_name,
            "custom_dish_price": float(custom_dish_price) if custom_dish_price else None,
        },
        severity="INFO",
    )

    return {
        "hitl_session_id": hitl_session_id,
        "chef_phone": chef_phone,
        "customer_phone": customer_phone,
        "decision": decision,
        "custom_dish_name": custom_dish_name,
        "custom_dish_price": custom_dish_price,
        "status": "RESOLVED",
    }


@tool("respond_to_custom_request_tool", args_schema=RespondToCustomRequestInput)
async def respond_to_custom_request_tool(
    hitl_session_id: str,
    chef_phone: str,
    decision: str,
    custom_dish_name: Optional[str] = None,
    custom_dish_price: Optional[Decimal] = None,
    chef_message: Optional[str] = None,
) -> str:
    """Respond to a customer's custom dish inquiry with an ACCEPTED, DECLINED, or COUNTER_OFFER decision, resolving the HITL pause session."""
    from app.db.session import transaction

    async with transaction() as session:
        res = await respond_to_custom_request(
            session,
            hitl_session_id=hitl_session_id,
            chef_phone=chef_phone,
            decision=decision,
            custom_dish_name=custom_dish_name,
            custom_dish_price=custom_dish_price,
            chef_message=chef_message,
        )
        price_str = f" @ ₹{res['custom_dish_price']:.2f}" if res['custom_dish_price'] else ""
        return (
            f"✅ Custom Request HITL Session [{res['hitl_session_id']}] RESOLVED!\n"
            f"Decision: {res['decision']}{price_str}\n"
            f"Customer ({res['customer_phone']}) notified on WhatsApp."
        )


# =============================================================================
# TOOL 10: get_assigned_driver_eta_tool
# =============================================================================
class GetAssignedDriverEtaInput(BaseModel):
    chef_phone: str = Field(
        ...,
        description="Normalized 10-digit phone number of home chef (e.g. '9876543210')",
    )
    order_id: Optional[str] = Field(
        default=None,
        description="Target CustomerOrder ID (e.g. 'ord_123456')",
    )
    service_date: Optional[str] = Field(
        default=None,
        description="Service date in ISO format YYYY-MM-DD (e.g. '2026-08-02')",
    )
    meal_window: Optional[str] = Field(
        default=None,
        description="'LUNCH' or 'DINNER'",
    )


async def get_assigned_driver_eta(
    session: AsyncSession,
    *,
    chef_phone: str,
    order_id: str | None = None,
    service_date: str | None = None,
    meal_window: str | None = None,
) -> dict[str, Any]:
    """Query assigned delivery driver info, pickup stop status, and estimated arrival time for a kitchen."""
    assert chef_phone and len(chef_phone) >= 10, f"Invalid chef_phone: {chef_phone}"
    assert order_id or (service_date and meal_window), "Must provide either order_id or both service_date and meal_window"

    chef = await session.get(ChefProfile, chef_phone)
    assert chef is not None, f"Kitchen profile not found for phone: {chef_phone}"

    from app.models.driver import DriverProfile
    from app.models.system import SystemDeliveryRoute, SystemDeliveryStop, SystemDeliveryStopOrder

    target_stop = None

    if order_id:
        stmt_so = select(SystemDeliveryStopOrder.stop_id).where(SystemDeliveryStopOrder.order_id == order_id)
        stop_id = (await session.execute(stmt_so)).scalar_one_or_none()
        if stop_id:
            target_stop = await session.get(SystemDeliveryStop, stop_id)
    else:
        date_obj = date.fromisoformat(service_date)
        stmt_stops = (
            select(SystemDeliveryStop)
            .join(SystemDeliveryRoute, SystemDeliveryStop.route_id == SystemDeliveryRoute.route_id)
            .where(
                SystemDeliveryStop.target_ref_id == chef_phone,
                SystemDeliveryStop.stop_type == "PICKUP_KITCHEN",
                SystemDeliveryRoute.service_date == date_obj,
                SystemDeliveryRoute.meal_window == meal_window,
            )
        )
        target_stop = (await session.execute(stmt_stops)).scalar_one_or_none()

    if target_stop is None:
        return {
            "chef_phone": chef_phone,
            "kitchen_name": chef.kitchen_name,
            "has_assigned_driver": False,
            "message": "No pickup delivery stop found for this kitchen and window yet.",
        }

    route = await session.get(SystemDeliveryRoute, target_stop.route_id)
    if route is None or not route.driver_phone:
        return {
            "chef_phone": chef_phone,
            "kitchen_name": chef.kitchen_name,
            "has_assigned_driver": False,
            "message": "Delivery route created but driver assignment is in progress.",
        }

    driver = await session.get(DriverProfile, route.driver_phone)
    driver_name = driver.driver_name if driver else "Assigned Driver"
    vehicle_info = f"{driver.vehicle_type} ({driver.vehicle_number})" if driver else "Vehicle Info"

    eta_time = target_stop.estimated_arrival.strftime("%I:%M %p") if target_stop.estimated_arrival else "Pending"

    return {
        "chef_phone": chef_phone,
        "kitchen_name": chef.kitchen_name,
        "has_assigned_driver": True,
        "driver_name": driver_name,
        "driver_phone": route.driver_phone,
        "vehicle_info": vehicle_info,
        "estimated_arrival": eta_time,
        "stop_status": target_stop.status,
        "route_status": route.status,
    }


@tool("get_assigned_driver_eta_tool", args_schema=GetAssignedDriverEtaInput)
async def get_assigned_driver_eta_tool(
    chef_phone: str,
    order_id: Optional[str] = None,
    service_date: Optional[str] = None,
    meal_window: Optional[str] = None,
) -> str:
    """Retrieve assigned driver contact info, vehicle details, and pickup ETA for a kitchen."""
    from app.db.session import SessionFactory

    async with SessionFactory() as session:
        data = await get_assigned_driver_eta(
            session,
            chef_phone=chef_phone,
            order_id=order_id,
            service_date=service_date,
            meal_window=meal_window,
        )
        if not data["has_assigned_driver"]:
            return f"ℹ️ {data['kitchen_name']}: {data['message']}"

        return (
            f"🛵 ASSIGNED DRIVER ETA FOR {data['kitchen_name']}:\n"
            f"Driver: {data['driver_name']} ({data['driver_phone']})\n"
            f"Vehicle: {data['vehicle_info']}\n"
            f"Estimated Arrival at Kitchen: {data['estimated_arrival']}\n"
            f"Pickup Status: {data['stop_status']} (Route {data['route_status']})"
        )



