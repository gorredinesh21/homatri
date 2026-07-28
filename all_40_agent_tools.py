"""
===============================================================================
Homaatri Production Blueprint: All 40 Agent LLM Tools Specification
===============================================================================
This file formally declares all 40 LLM Tools across Chef, Customer, Master,
and Delivery Driver agents using Pydantic schemas and Python function stubs.

Cross-domain tools explicitly highlight inside their function body how they emit
decoupled event payloads targeting the Master Agent / next Agent node!
===============================================================================
"""

from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field
from langchain_core.tools import tool


# =============================================================================
# SECTION 1: CHEF AGENT TOOLS (9 TOOLS)
# =============================================================================

# --- Tool 1: get_chef_profile_tool ---
class GetChefProfileInput(BaseModel):
    chef_phone: str = Field(..., description="Chef WhatsApp phone number in E.164 format")

@tool("get_chef_profile_tool", args_schema=GetChefProfileInput)
def get_chef_profile_tool(chef_phone: str) -> Dict[str, Any]:
    """[STANDALONE] Identifies an onboarded home-cook chef on incoming WhatsApp webhooks."""
    # DB Read: SELECT * FROM chef_profiles WHERE phone_number = chef_phone
    return {"chef_phone": chef_phone, "kitchen_name": "Ramesh Kitchen", "status": "ACTIVE"}


# --- Tool 2: set_daily_dish_capacity_tool ---
class SetDailyCapacityInput(BaseModel):
    chef_phone: str = Field(..., description="Chef WhatsApp phone number")
    menu_item_id: str = Field(..., description="Menu item ID")
    date: str = Field(..., description="Date in YYYY-MM-DD format")
    max_capacity: int = Field(..., gt=0, description="Maximum meal prep limit")

@tool("set_daily_dish_capacity_tool", args_schema=SetDailyCapacityInput)
def set_daily_dish_capacity_tool(chef_phone: str, menu_item_id: str, date: str, max_capacity: int) -> Dict[str, Any]:
    """[STANDALONE] Sets maximum meal prep limit for a dish for a specific date."""
    # DB Write: INSERT INTO chef_daily_inventory
    return {"chef_phone": chef_phone, "menu_item_id": menu_item_id, "max_capacity": max_capacity, "status": "SUCCESS"}


# --- Tool 3: toggle_dish_stock_tool ---
class ToggleStockInput(BaseModel):
    chef_phone: str = Field(..., description="Chef WhatsApp phone number")
    menu_item_id: str = Field(..., description="Menu item ID")
    is_available: bool = Field(..., description="True for IN_STOCK, False for OUT_OF_STOCK")

@tool("toggle_dish_stock_tool", args_schema=ToggleStockInput)
def toggle_dish_stock_tool(chef_phone: str, menu_item_id: str, is_available: bool) -> Dict[str, Any]:
    """[STANDALONE] Instantly marks a dish IN_STOCK or OUT_OF_STOCK mid-day."""
    # DB Write: UPDATE chef_menu_items SET is_available = is_available
    return {"menu_item_id": menu_item_id, "is_available": is_available, "status": "UPDATED"}


# --- Tool 4: check_daily_inventory_status_tool ---
class CheckInventoryInput(BaseModel):
    chef_phone: str = Field(..., description="Chef WhatsApp phone number")
    date: str = Field(..., description="Date in YYYY-MM-DD format")

@tool("check_daily_inventory_status_tool", args_schema=CheckInventoryInput)
def check_daily_inventory_status_tool(chef_phone: str, date: str) -> List[Dict[str, Any]]:
    """[STANDALONE] Displays remaining meal prep capacity and units sold for today's batch."""
    # DB Read: SELECT * FROM chef_daily_inventory
    return [{"dish_name": "Paneer Thali", "max_capacity": 15, "units_sold": 8, "remaining_slots": 7}]


# --- Tool 5: get_chef_batch_checklist_tool (MERGED TOOL) ---
class GetBatchChecklistInput(BaseModel):
    chef_phone: str = Field(..., description="Chef WhatsApp phone number")
    meal_window: str = Field(..., description="'LUNCH' or 'DINNER'")
    date: str = Field(..., description="Date in YYYY-MM-DD format")

@tool("get_chef_batch_checklist_tool", args_schema=GetBatchChecklistInput)
def get_chef_batch_checklist_tool(chef_phone: str, meal_window: str, date: str) -> Dict[str, Any]:
    """[STANDALONE] Generates summary cooking totals AND itemized order checklists at cutoff."""
    # DB Read: Global Read across customer_orders & customer_order_items
    return {"meal_window": meal_window, "total_meals_to_cook": 18, "summary_counts": {"Paneer Thali": 12, "Veg Thali": 6}}


# --- Tool 6: mark_order_packed_ready_tool (CROSS-DOMAIN LINKED) ---
class MarkOrderPackedInput(BaseModel):
    chef_phone: str = Field(..., description="Chef WhatsApp phone number")
    order_id: str = Field(..., description="Order ID")

@tool("mark_order_packed_ready_tool", args_schema=MarkOrderPackedInput)
def mark_order_packed_ready_tool(chef_phone: str, order_id: str) -> Dict[str, Any]:
    """[CROSS-DOMAIN LINKED] Broadcasts food packed signal, triggering Master Agent -> Driver Agent notification."""
    # DB Write: UPDATE customer_orders SET status = 'PACKED'
    
    # 🔗 CROSS-DOMAIN EVENT DISPATCH:
    print("   [CROSS-DOMAIN HANDOFF] Chef Tool -> Master Agent (relay_order_ready_to_driver_tool)")
    return {
        "event_type": "ORDER_PACKED_READY",
        "order_id": order_id,
        "chef_phone": chef_phone,
        "next_target_agent": "MASTER",  # Triggers MasterAgent -> DriverAgent
        "status": "PACKED_READY"
    }


# --- Tool 7: get_assigned_driver_info_tool (CROSS-DOMAIN LINKED) ---
class GetAssignedDriverInput(BaseModel):
    chef_phone: str = Field(..., description="Chef WhatsApp phone number")
    meal_window: str = Field(..., description="'LUNCH' or 'DINNER'")
    date: str = Field(..., description="Date in YYYY-MM-DD format")

@tool("get_assigned_driver_info_tool", args_schema=GetAssignedDriverInput)
def get_assigned_driver_info_tool(chef_phone: str, meal_window: str, date: str) -> Dict[str, Any]:
    """[CROSS-DOMAIN LINKED] Queries Master Agent delivery stop state for assigned driver arrival info."""
    # 🔗 CROSS-DOMAIN READ BRIDGE:
    print("   [CROSS-DOMAIN READ] Chef Tool -> Master Agent (system_delivery_stops & driver_profiles)")
    return {"driver_name": "Vikram", "driver_phone": "+919988776655", "pickup_eta": "12:45 PM"}


# --- Tool 8: respond_to_custom_request_tool (COUNTER-OFFER PROTOCOL) ---
class RespondCustomRequestInput(BaseModel):
    chef_phone: str = Field(..., description="Chef WhatsApp phone number")
    order_id: str = Field(..., description="Order ID")
    decision: str = Field(..., description="'ACCEPTED', 'DECLINED', or 'COUNTER_OFFER'")
    counter_offer_text: Optional[str] = Field(None, description="Counter-offer details (e.g. '2 rotis instead of 3')")

@tool("respond_to_custom_request_tool", args_schema=RespondCustomRequestInput)
def respond_to_custom_request_tool(chef_phone: str, order_id: str, decision: str, counter_offer_text: Optional[str] = None) -> Dict[str, Any]:
    """[CROSS-DOMAIN LINKED] Resumes LangGraph interrupt() checkpoint, relaying decision/counter-offer via Master -> Customer Agent."""
    # 🔗 CROSS-DOMAIN DOUBLE-HITL RELAY:
    print("   [CROSS-DOMAIN HANDOFF] Chef Tool -> Master Agent -> Customer Agent (Counter-Offer Relay)")
    return {
        "event_type": "DIETARY_COUNTER_OFFER",
        "order_id": order_id,
        "decision": decision,
        "counter_offer_text": counter_offer_text,
        "next_target_agent": "MASTER",  # Triggers MasterAgent -> CustomerAgent
        "resumed_graph": True
    }


# --- Tool 9: check_driver_arrival_status_tool ---
class CheckDriverArrivalInput(BaseModel):
    chef_phone: str = Field(..., description="Chef WhatsApp phone number")
    date: str = Field(..., description="Date in YYYY-MM-DD format")

@tool("check_driver_arrival_status_tool", args_schema=CheckDriverArrivalInput)
def check_driver_arrival_status_tool(chef_phone: str, date: str) -> Dict[str, Any]:
    """[CROSS-DOMAIN LINKED] Queries Master Agent for assigned driver arrival timestamp outside kitchen."""
    print("   [CROSS-DOMAIN READ] Chef Tool -> Master Agent (system_delivery_stops arrival timestamp)")
    return {"driver_name": "Vikram", "has_arrived": True, "arrived_at": "12:42 PM"}


# =============================================================================
# SECTION 2: CUSTOMER AGENT TOOLS (11 TOOLS)
# =============================================================================

class GetCustomerProfileInput(BaseModel):
    customer_phone: str = Field(..., description="Customer WhatsApp phone number")

@tool("get_customer_profile_tool", args_schema=GetCustomerProfileInput)
def get_customer_profile_tool(customer_phone: str) -> Dict[str, Any]:
    """[STANDALONE] Identifies customer on incoming WhatsApp webhooks."""
    return {"customer_phone": customer_phone, "name": "Dinesh", "is_registered": True}


class RegisterCustomerProfileInput(BaseModel):
    customer_phone: str = Field(..., description="Customer WhatsApp phone number")
    name: str = Field(..., description="Customer full name")
    delivery_address: str = Field(..., description="Text delivery address")

@tool("register_customer_profile_tool", args_schema=RegisterCustomerProfileInput)
def register_customer_profile_tool(customer_phone: str, name: str, delivery_address: str) -> Dict[str, Any]:
    """[STANDALONE] 2-Step Onboarding: Saves text name/address, prompts user on WhatsApp for location pin attachment."""
    return {"customer_phone": customer_phone, "status": "PENDING_LOCATION_PIN"}


class UpdateCustomerLocationInput(BaseModel):
    customer_phone: str = Field(..., description="Customer WhatsApp phone number")
    latitude: float = Field(..., description="GPS Latitude")
    longitude: float = Field(..., description="GPS Longitude")

@tool("update_customer_location_pin_tool", args_schema=UpdateCustomerLocationInput)
def update_customer_location_pin_tool(customer_phone: str, latitude: float, longitude: float) -> Dict[str, Any]:
    """[STANDALONE] Receives WhatsApp location pin attachment & completes customer profile registration."""
    return {"customer_phone": customer_phone, "latitude": latitude, "longitude": longitude, "status": "REGISTERED_ACTIVE"}


class FindNearbyKitchensInput(BaseModel):
    customer_phone: str = Field(..., description="Customer WhatsApp phone number")
    meal_window: str = Field(..., description="'LUNCH' or 'DINNER'")

@tool("find_nearby_home_kitchens_tool", args_schema=FindNearbyKitchensInput)
def find_nearby_home_kitchens_tool(customer_phone: str, meal_window: str) -> List[Dict[str, Any]]:
    """[STANDALONE] Finds active home kitchens sorted from closest to farthest using Haversine math."""
    return [{"chef_phone": "+919876543210", "kitchen_name": "Ramesh Kitchen", "distance_km": 1.4}]


class ViewChefMenuInput(BaseModel):
    chef_phone: str = Field(..., description="Chef WhatsApp phone number")
    meal_window: str = Field(..., description="'LUNCH' or 'DINNER'")

@tool("view_chef_menu_tool", args_schema=ViewChefMenuInput)
def view_chef_menu_tool(chef_phone: str, meal_window: str) -> Dict[str, Any]:
    """[STANDALONE] Displays full dish catalog, prices, and remaining inventory slots for a selected chef."""
    return {"chef_phone": chef_phone, "menu_items": [{"menu_item_id": "item_201", "dish_name": "Paneer Thali", "unit_price": 180.0}]}


class InitCustomerOrderInput(BaseModel):
    customer_phone: str = Field(..., description="Customer WhatsApp phone number")
    chef_phone: str = Field(..., description="Chef WhatsApp phone number")
    meal_window: str = Field(..., description="'LUNCH' or 'DINNER'")
    items: List[Dict[str, Any]] = Field(..., description="Initial cart items list")

@tool("initialize_customer_order_tool", args_schema=InitCustomerOrderInput)
def initialize_customer_order_tool(customer_phone: str, chef_phone: str, meal_window: str, items: List[Dict[str, Any]]) -> Dict[str, Any]:
    """[CROSS-DOMAIN LINKED] Atomic 1-Step Order Creation: Validates cutoff clock via Master Agent and creates order header + items."""
    # 🔗 CROSS-DOMAIN QUERY:
    print("   [CROSS-DOMAIN QUERY] Customer Tool -> Master Agent (validate_meal_cutoff_clock_tool)")
    return {"order_id": "ord_104", "status": "PENDING_PAYMENT", "cart_subtotal": 360.0}


class AddItemToOrderInput(BaseModel):
    customer_phone: str = Field(..., description="Customer WhatsApp phone number")
    order_id: str = Field(..., description="Order ID")
    menu_item_id: str = Field(..., description="Menu item ID")
    quantity: int = Field(..., gt=0, description="Item quantity")

@tool("add_item_to_order_tool", args_schema=AddItemToOrderInput)
def add_item_to_order_tool(customer_phone: str, order_id: str, menu_item_id: str, quantity: int) -> Dict[str, Any]:
    """[STANDALONE] Appends extra dishes to an existing draft order in later turns."""
    return {"order_id": order_id, "item_added": menu_item_id, "quantity": quantity, "status": "ITEM_ADDED"}


class GeneratePaymentLinkInput(BaseModel):
    customer_phone: str = Field(..., description="Customer WhatsApp phone number")
    order_id: str = Field(..., description="Order ID")

@tool("generate_payment_link_tool", args_schema=GeneratePaymentLinkInput)
def generate_payment_link_tool(customer_phone: str, order_id: str) -> Dict[str, Any]:
    """[CROSS-DOMAIN LINKED] Unified Billing: Calculates unpaid balance (initial vs top-up) and generates payment link via Master Agent."""
    # 🔗 CROSS-DOMAIN EXECUTION:
    print("   [CROSS-DOMAIN HANDOFF] Customer Tool -> Master Agent (Generate UPI Payment Link)")
    return {"order_id": order_id, "payment_type": "INITIAL", "amount_due": 390.0, "payment_link_url": "https://pay.homatri.com/p901"}


class GetActiveOrderStatusInput(BaseModel):
    customer_phone: str = Field(..., description="Customer WhatsApp phone number")

@tool("get_active_order_status_tool", args_schema=GetActiveOrderStatusInput)
def get_active_order_status_tool(customer_phone: str) -> Dict[str, Any]:
    """[CROSS-DOMAIN LINKED] Queries operational state across Chef readiness, Driver location, and stop ETAs."""
    # 🔗 CROSS-DOMAIN READ:
    print("   [CROSS-DOMAIN READ] Customer Tool -> Master Agent (Join customer_orders + chef_readiness + driver_locations)")
    return {"order_id": "ord_104", "order_status": "PICKED_UP", "driver_name": "Vikram", "eta": "1:15 PM"}


class GetOrderHistoryInput(BaseModel):
    customer_phone: str = Field(..., description="Customer WhatsApp phone number")

@tool("get_order_history_tool", args_schema=GetOrderHistoryInput)
def get_order_history_tool(customer_phone: str) -> List[Dict[str, Any]]:
    """[STANDALONE] Retrieves past completed order receipts for a customer."""
    return [{"order_id": "ord_104", "date": "2026-07-28", "total_paid": 390.0}]


class SubmitOrderReviewInput(BaseModel):
    customer_phone: str = Field(..., description="Customer WhatsApp phone number")
    order_id: str = Field(..., description="Order ID")
    chef_rating: int = Field(..., ge=1, le=5, description="Chef rating (1-5)")
    driver_rating: int = Field(..., ge=1, le=5, description="Driver rating (1-5)")

@tool("submit_order_review_tool", args_schema=SubmitOrderReviewInput)
def submit_order_review_tool(customer_phone: str, order_id: str, chef_rating: int, driver_rating: int) -> Dict[str, Any]:
    """[STANDALONE] Records post-delivery ratings for chef and driver."""
    return {"order_id": order_id, "chef_rating": chef_rating, "driver_rating": driver_rating, "status": "REVIEW_SAVED"}


# =============================================================================
# SECTION 3: MASTER AGENT TOOLS (12 TOOLS)
# =============================================================================

class ValidateCutoffClockInput(BaseModel):
    meal_window: str = Field(..., description="'LUNCH' or 'DINNER'")

@tool("validate_meal_cutoff_clock_tool", args_schema=ValidateCutoffClockInput)
def validate_meal_cutoff_clock_tool(meal_window: str) -> Dict[str, Any]:
    """[STANDALONE] Checks if meal window cutoff clock is open (<= 12 PM Lunch / <= 7 PM Dinner)."""
    return {"meal_window": meal_window, "is_open": True, "cutoff_time": "12:00 PM"}


class ExecuteCutoffBatchInput(BaseModel):
    meal_window: str = Field(..., description="'LUNCH' or 'DINNER'")
    date: str = Field(..., description="Date in YYYY-MM-DD format")

@tool("execute_cutoff_batch_and_route_optimization_tool", args_schema=ExecuteCutoffBatchInput)
def execute_cutoff_batch_and_route_optimization_tool(meal_window: str, date: str) -> Dict[str, Any]:
    """[MASTER CORE ENGINE SERVICE] Locks batch window, calls GCP Route Optimization API (ONCE), saves stops, and signals Chef & Driver Agents."""
    # 🔗 MASTER CORE ENGINE EXECUTION:
    print("   [CORE ENGINE EXECUTION] Master Agent -> GCP Route Optimization API (ONCE at Cutoff)")
    print("   [CROSS-DOMAIN DISPATCH] Master Agent -> Dispatches Chef checklists & Driver itineraries")
    return {"route_id": "rt_801", "total_stops": 6, "status": "BATCH_LOCKED_AND_ROUTE_OPTIMIZED"}


class RelayDietaryRequestInput(BaseModel):
    customer_phone: str = Field(..., description="Customer WhatsApp phone number")
    order_id: str = Field(..., description="Order ID")
    dietary_notes: str = Field(..., description="Dietary notes (e.g. 'No garlic')")

@tool("relay_dietary_request_to_chef_tool", args_schema=RelayDietaryRequestInput)
def relay_dietary_request_to_chef_tool(customer_phone: str, order_id: str, dietary_notes: str) -> Dict[str, Any]:
    """[CROSS-DOMAIN LINKED] Relays customer dietary request to Chef Agent and triggers LangGraph interrupt()."""
    print("   [CROSS-DOMAIN HANDOFF] Master Agent -> LangGraph interrupt() -> Chef Agent (WhatsApp Prompt)")
    return {"order_id": order_id, "next_target_agent": "CHEF", "status": "INTERRUPTED_WAITING_CHEF"}


class ProcessCancellationInput(BaseModel):
    customer_phone: str = Field(..., description="Customer WhatsApp phone number")
    order_id: str = Field(..., description="Order ID")
    cancellation_reason: str = Field(..., description="Reason for cancellation")

@tool("process_order_cancellation_tool", args_schema=ProcessCancellationInput)
def process_order_cancellation_tool(customer_phone: str, order_id: str, cancellation_reason: str) -> Dict[str, Any]:
    """[STANDALONE] Evaluates cancellation rules: auto-cancels & refunds if before cutoff."""
    return {"order_id": order_id, "status": "CANCELLED_REFUNDED", "refund_amount": 390.0}


class RelayOrderReadyInput(BaseModel):
    chef_phone: str = Field(..., description="Chef WhatsApp phone number")
    order_id: str = Field(..., description="Order ID")

@tool("relay_order_ready_to_driver_tool", args_schema=RelayOrderReadyInput)
def relay_order_ready_to_driver_tool(chef_phone: str, order_id: str) -> Dict[str, Any]:
    """[CROSS-DOMAIN LINKED] Relays food packed signal from Chef Agent to Driver Agent."""
    print("   [CROSS-DOMAIN HANDOFF] Master Agent -> Driver Agent (Pickup Notification)")
    return {"order_id": order_id, "next_target_agent": "DRIVER", "driver_notified": True}


class RelayGateDeliveryInput(BaseModel):
    driver_phone: str = Field(..., description="Driver WhatsApp phone number")
    order_ids_list: List[str] = Field(..., description="Delivered order IDs")
    apartment_gate_name: str = Field(..., description="Security Gate location name")

@tool("relay_gate_delivery_completed_tool", args_schema=RelayGateDeliveryInput)
def relay_gate_delivery_completed_tool(driver_phone: str, order_ids_list: List[str], apartment_gate_name: str) -> Dict[str, Any]:
    """[CROSS-DOMAIN LINKED] Relays gate delivery completion from Driver Agent -> Customer Agent."""
    print("   [CROSS-DOMAIN HANDOFF] Master Agent -> Customer Agent (WhatsApp Gate Delivery Alerts)")
    return {"order_ids_notified": order_ids_list, "next_target_agent": "CUSTOMER", "status": "CUSTOMERS_NOTIFIED"}


class RelayUnlocatableAddressInput(BaseModel):
    driver_phone: str = Field(..., description="Driver WhatsApp phone number")
    order_id: str = Field(..., description="Order ID")

@tool("relay_unlocatable_address_request_tool", args_schema=RelayUnlocatableAddressInput)
def relay_unlocatable_address_request_tool(driver_phone: str, order_id: str) -> Dict[str, Any]:
    """[CROSS-DOMAIN LINKED] Relays address missing alert from Driver Agent -> Customer Agent location pin request."""
    print("   [CROSS-DOMAIN HANDOFF] Master Agent -> LangGraph interrupt() -> Customer Agent (Location Pin Prompt)")
    return {"order_id": order_id, "next_target_agent": "CUSTOMER", "status": "WAITING_LOCATION_PIN"}


class RelayTrafficDelayInput(BaseModel):
    driver_phone: str = Field(..., description="Driver WhatsApp phone number")
    route_id: str = Field(..., description="Route ID")
    delay_minutes: int = Field(..., description="Delay duration in minutes")

@tool("relay_traffic_delay_alert_tool", args_schema=RelayTrafficDelayInput)
def relay_traffic_delay_alert_tool(driver_phone: str, route_id: str, delay_minutes: int) -> Dict[str, Any]:
    """[CROSS-DOMAIN LINKED] Recalculates ETAs in system_delivery_stops and alerts affected customers via Customer Agent."""
    print("   [CROSS-DOMAIN HANDOFF] Master Agent -> Customer Agent (Traffic Delay Alerts)")
    return {"route_id": route_id, "delay_minutes": delay_minutes, "status": "CUSTOMERS_ALERTED"}


class ProcessPaymentWebhookInput(BaseModel):
    payment_id: str = Field(..., description="Payment ID")
    transaction_id: str = Field(..., description="Gateway Transaction ID")
    status: str = Field(..., description="'PAID' or 'FAILED'")

@tool("process_payment_gateway_webhook_tool", args_schema=ProcessPaymentWebhookInput)
def process_payment_gateway_webhook_tool(payment_id: str, transaction_id: str, status: str) -> Dict[str, Any]:
    """[CROSS-DOMAIN LINKED] Verifies Razorpay/Stripe webhook HMAC signature and updates order status to CONFIRMED."""
    print("   [WEBHOOK INGRESS] Payment Gateway -> Master Agent -> Updates customer_orders status = CONFIRMED")
    return {"payment_id": payment_id, "order_status": "CONFIRMED", "payment_status": "PAID"}


class DelegateWriteInput(BaseModel):
    requesting_role: str = Field(..., description="'CUSTOMER', 'CHEF', or 'DRIVER'")
    target_role: str = Field(..., description="'CUSTOMER', 'CHEF', or 'DRIVER'")
    target_table: str = Field(..., description="Database table name")
    payload: Dict[str, Any] = Field(..., description="Write data payload")

@tool("delegate_cross_domain_write_tool", args_schema=DelegateWriteInput)
def delegate_cross_domain_write_tool(requesting_role: str, target_role: str, target_table: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """[CROSS-DOMAIN LINKED] Enforces write delegation protocol, logging audit event and delegating write to target agent."""
    print(f"   [WRITE DELEGATION] Master Agent delegating write from {requesting_role} -> {target_role} ({target_table})")
    return {"requesting_role": requesting_role, "target_role": target_role, "status": "DELEGATED_SUCCESSFULLY"}


class DispatchWhatsAppInput(BaseModel):
    recipient_phone: str = Field(..., description="Recipient phone number")
    message_text: str = Field(..., description="Message content")
    recipient_role: str = Field(..., description="'CUSTOMER', 'CHEF', or 'DRIVER'")

@tool("dispatch_whatsapp_outbound_message_tool", args_schema=DispatchWhatsAppInput)
def dispatch_whatsapp_outbound_message_tool(recipient_phone: str, message_text: str, recipient_role: str) -> Dict[str, Any]:
    """[STANDALONE] Pushes outbound WhatsApp messages to system_outbound_queue and calls Meta Cloud API."""
    return {"recipient_phone": recipient_phone, "status": "QUEUED_AND_DISPATCHED"}


class LogAuditInput(BaseModel):
    event_type: str = Field(..., description="Audit event type")
    source_role: str = Field(..., description="Source agent role")
    payload: Dict[str, Any] = Field(..., description="Event payload")
    severity: str = Field(..., description="'INFO', 'WARNING', or 'CRITICAL'")

@tool("log_system_audit_event_tool", args_schema=LogAuditInput)
def log_system_audit_event_tool(event_type: str, source_role: str, payload: Dict[str, Any], severity: str) -> Dict[str, Any]:
    """[STANDALONE] Writes immutable audit log in system_agent_logs."""
    return {"event_type": event_type, "status": "LOGGED_SUCCESSFULLY"}


# =============================================================================
# SECTION 4: DELIVERY DRIVER AGENT TOOLS (8 TOOLS)
# =============================================================================

class GetDriverProfileInput(BaseModel):
    driver_phone: str = Field(..., description="Driver WhatsApp phone number")

@tool("get_driver_profile_tool", args_schema=GetDriverProfileInput)
def get_driver_profile_tool(driver_phone: str) -> Dict[str, Any]:
    """[STANDALONE] Identifies an onboarded delivery driver on incoming WhatsApp webhooks."""
    return {"driver_phone": driver_phone, "driver_name": "Vikram", "status": "ACTIVE"}


class GetAssignedRouteInput(BaseModel):
    driver_phone: str = Field(..., description="Driver WhatsApp phone number")
    meal_window: str = Field(..., description="'LUNCH' or 'DINNER'")
    date: str = Field(..., description="Date in YYYY-MM-DD format")

@tool("get_assigned_route_itinerary_tool", args_schema=GetAssignedRouteInput)
def get_assigned_route_itinerary_tool(driver_phone: str, meal_window: str, date: str) -> Dict[str, Any]:
    """[CROSS-DOMAIN LINKED] Queries Master Agent for the full stop sequence generated by GCP Route Optimization API."""
    print("   [CROSS-DOMAIN READ] Driver Tool -> Master Agent (system_delivery_routes & system_delivery_stops)")
    return {"driver_phone": driver_phone, "route_id": "rt_801", "total_stops": 6}


class DispatchNextLegInput(BaseModel):
    driver_phone: str = Field(..., description="Driver WhatsApp phone number")
    current_stop_index: int = Field(..., description="Current stop index")

@tool("dispatch_next_leg_navigation_link_tool", args_schema=DispatchNextLegInput)
def dispatch_next_leg_navigation_link_tool(driver_phone: str, current_stop_index: int) -> Dict[str, Any]:
    """[STANDALONE] Generates and sends a single-leg Google Maps link for the VERY NEXT STOP (Stop N -> Stop N+1)."""
    return {"next_stop_index": current_stop_index + 1, "single_leg_url": "https://maps.google.com/?daddr=MyHomeBhooja"}


class MarkDriverReachedInput(BaseModel):
    driver_phone: str = Field(..., description="Driver WhatsApp phone number")
    stop_index: int = Field(..., description="Stop index")

@tool("mark_driver_reached_stop_tool", args_schema=MarkDriverReachedInput)
def mark_driver_reached_stop_tool(driver_phone: str, stop_index: int) -> Dict[str, Any]:
    """[STANDALONE] Triggered when driver messages 'Reached' or taps button 'I Have Arrived' at a stop."""
    return {"driver_phone": driver_phone, "stop_index": stop_index, "status": "ARRIVED_AT_STOP"}


class MarkOrdersPickedUpInput(BaseModel):
    driver_phone: str = Field(..., description="Driver WhatsApp phone number")
    stop_index: int = Field(..., description="Stop index")
    order_ids_list: List[str] = Field(..., description="List of picked up order IDs")

@tool("mark_orders_picked_up_tool", args_schema=MarkOrdersPickedUpInput)
def mark_orders_picked_up_tool(driver_phone: str, stop_index: int, order_ids_list: List[str]) -> Dict[str, Any]:
    """[CROSS-DOMAIN LINKED] Updates order status = PICKED_UP and automatically calls dispatch_next_leg_navigation_link_tool."""
    print("   [CROSS-DOMAIN WRITE] Driver Tool -> Updates customer_orders status = PICKED_UP")
    return {"stop_index": stop_index, "orders_picked_up": order_ids_list, "status": "FOOD_PICKED_UP"}


class MarkGateDeliveryCompletedInput(BaseModel):
    driver_phone: str = Field(..., description="Driver WhatsApp phone number")
    stop_index: int = Field(..., description="Stop index")
    order_ids_list: List[str] = Field(..., description="Delivered order IDs")
    left_with_security: bool = Field(..., description="True if dropped at Security Guard")

@tool("mark_gate_delivery_completed_tool", args_schema=MarkGateDeliveryCompletedInput)
def mark_gate_delivery_completed_tool(driver_phone: str, stop_index: int, order_ids_list: List[str], left_with_security: bool) -> Dict[str, Any]:
    """[CROSS-DOMAIN LINKED] Triggered when driver drops off multi-order food packages at an Apartment Security Gate, alerting Master -> Customer Agent."""
    print("   [CROSS-DOMAIN HANDOFF] Driver Tool -> Master Agent (relay_gate_delivery_completed_tool) -> Customer Agent")
    return {"stop_index": stop_index, "orders_delivered": order_ids_list, "next_target_agent": "MASTER", "status": "GATE_DELIVERY_COMPLETED"}


class ReportUnlocatableAddressInput(BaseModel):
    driver_phone: str = Field(..., description="Driver WhatsApp phone number")
    stop_index: int = Field(..., description="Stop index")
    order_id: str = Field(..., description="Order ID")

@tool("report_unlocatable_address_hitl_tool", args_schema=ReportUnlocatableAddressInput)
def report_unlocatable_address_hitl_tool(driver_phone: str, stop_index: int, order_id: str) -> Dict[str, Any]:
    """[CROSS-DOMAIN LINKED] Pauses graph execution via LangGraph interrupt() and alerts Master Agent -> Customer Agent to request location pin."""
    print("   [CROSS-DOMAIN HANDOFF] Driver Tool -> Master Agent -> LangGraph interrupt() -> Customer Agent (Location Pin Prompt)")
    return {"order_id": order_id, "next_target_agent": "MASTER", "hitl_status": "INTERRUPTED_WAITING_LOCATION_PIN"}


class ReportVehicleDelayInput(BaseModel):
    driver_phone: str = Field(..., description="Driver WhatsApp phone number")
    delay_minutes: int = Field(..., description="Delay duration in minutes")
    delay_reason: str = Field(..., description="Reason for delay")

@tool("report_vehicle_delay_alert_tool", args_schema=ReportVehicleDelayInput)
def report_vehicle_delay_alert_tool(driver_phone: str, delay_minutes: int, delay_reason: str) -> Dict[str, Any]:
    """[CROSS-DOMAIN LINKED] Triggered when driver reports traffic or breakdown, recalculating ETAs via Master Agent."""
    print("   [CROSS-DOMAIN HANDOFF] Driver Tool -> Master Agent (relay_traffic_delay_alert_tool) -> Customer Agent")
    return {"driver_phone": driver_phone, "delay_minutes": delay_minutes, "next_target_agent": "MASTER", "status": "ETA_UPDATED"}


# =============================================================================
# SUMMARY VERIFICATION
# =============================================================================
if __name__ == "__main__":
    print("=======================================================================")
    print("✅ HOMAATRI PRODUCTION BLUEPRINT: ALL 40 LLM TOOLS DEFINED SUCCESSFULLY!")
    print("   - Chef Agent: 9 Tools")
    print("   - Customer Agent: 11 Tools")
    print("   - Master Agent: 12 Tools")
    print("   - Delivery Driver Agent: 8 Tools")
    print("   Total Declared Production Tools: 40 Tools")
    print("=======================================================================")
