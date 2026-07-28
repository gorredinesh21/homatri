# 👑 Master Specification: The Master Agent Tools

This document outlines the complete persona, communication rules, categories, and **12 Final Production LLM Tool Specifications** for the **Master Agent** in Homaatri.

---

## 🎭 1. Persona & Communication Rules
* **Human Persona**: Highly disciplined, objective, structured, and authoritative. Acts like the Chief Operating Officer (COO) + General Manager + Security Officer.
* **Core Rule**: Has **Global Write Authority** across all 18 tables. Enforces all system policies, validates subagent delegation requests, logs every cross-domain action, and controls the cutoff clock.

---

## 🛠️ 2. Production Toolset Specification (12 Tools)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                            MASTER AGENT TOOLSET                             │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │
 ┌─────────────────┬─────────────────┬─┴───────────────┬─────────────────┬─────────────────┐
 ▼                 ▼                 ▼                 ▼                 ▼                 ▼
CAT 1 & 2: CUTOFF  CAT 3: INTER-AGENT  CAT 3: HITL &     CAT 4: FINANCIAL  CAT 5: WRITE DELEGATION
CLOCK & ROUTE API  CROSS-DOMAIN RELAYS EXCEPTION RELAYS  WEBHOOK ENGINE    & AUDIT LOGGING
• Validate Cutoff  • Dietary Relay     • Gate Delivery   • Payment Webhook • Write Delegation
• Atomic Cutoff &  • Cancellation      • Address Pin     • Dispatch WA Queue• System Audit
  Route Solver       Check             • Traffic Delay   • Log Audit Event
```

---

### ⏱️ CATEGORY 1 & 2: Cutoff Clock & GCP Route Optimization Engine

#### Tool 1: `validate_meal_cutoff_clock_tool`
* **Action Source**: Action 1 (`validate_meal_window_cutoff_clock`)
* **Purpose**: Called during customer chat turns to check if the meal window is open or closed before taking an order ($\le$ 12:00 PM for Lunch, $\le$ 7:00 PM for Dinner).
* **Inputs**:
  - `meal_window`: `str` (Required, `'LUNCH'` or `'DINNER'`)
* **Expected Output Structure**:
  ```json
  {
    "meal_window": "LUNCH",
    "is_open": true,
    "cutoff_time": "12:00 PM",
    "time_remaining_minutes": 15,
    "status_message": "Lunch window is open. 15 minutes remaining before 12:00 PM cutoff."
  }
  ```
* **DB Read**: `SELECT * FROM system_meal_windows WHERE ...` (Read)

---

#### Tool 2: `execute_cutoff_batch_and_route_optimization_tool` *(MERGED ATOMIC TOOL)*
* **Action Source**: Merges Action 2 (`lock_meal_window_batch`) + Action 3 (`orchestrate_gcp_route_optimization`)
* **Purpose**: Executed automatically at 12:00 PM & 7:00 PM cutoff. Atomically locks the meal window (`LOCKED_PROCESSING`), merges orders with identical apartment gate coordinates into single stops, calls GCP Route Optimization API (ONCE), and saves master routes in DB.
* **Inputs**:
  - `meal_window`: `str` (Required, `'LUNCH'` or `'DINNER'`)
  - `date`: `str` (Required, `'YYYY-MM-DD'`)
* **Expected Output Structure**:
  ```json
  {
    "meal_window": "LUNCH",
    "date": "2026-07-29",
    "batch_status": "LOCKED_PROCESSING",
    "total_confirmed_orders": 18,
    "route_id": "rt_801",
    "total_stops": 6,
    "apartment_gate_consolidated_stops": 4,
    "assigned_driver_phone": "+919988776655",
    "status": "BATCH_LOCKED_AND_ROUTE_OPTIMIZED"
  }
  ```
* **DB Write**: `UPDATE system_meal_windows`, `INSERT INTO system_delivery_routes` & `system_delivery_stops` (Write)

---

### 🔄 CATEGORY 3: Inter-Agent Cross-Domain Message Relays (Mediator Duty)

#### Tool 3: `relay_dietary_request_to_chef_tool`
* **Action Source**: Action 4 (`relay_customer_dietary_request_to_chef`)
* **Purpose**: Relays a customer's mid-cooking dietary request (*"No garlic"*) to the Chef Agent and triggers LangGraph `interrupt()` for real Chef approval on WhatsApp.
* **Inputs**:
  - `customer_phone`: `str` (Required)
  - `order_id`: `str` (Required)
  - `dietary_notes`: `str` (Required)
* **Expected Output Structure**:
  ```json
  {
    "order_id": "ord_104",
    "chef_notified": true,
    "status": "INTERRUPTED_WAITING_CHEF_RESPONSE"
  }
  ```
* **DB Write**: `INSERT INTO system_agent_logs ...` (Write)

---

#### Tool 4: `process_order_cancellation_tool`
* **Action Source**: Action 5 (`process_order_cancellation_request`)
* **Purpose**: Evaluates cancellation requests against platform rules. If before cutoff (12 PM / 7 PM) $\rightarrow$ Auto-cancels & triggers refund; if after cutoff & cooking $\rightarrow$ Rejects cancellation.
* **Inputs**:
  - `customer_phone`: `str` (Required)
  - `order_id`: `str` (Required)
  - `cancellation_reason`: `str` (Required)
* **Expected Output Structure**:
  ```json
  {
    "order_id": "ord_104",
    "status": "CANCELLED_REFUNDED",
    "refund_amount": 390.00,
    "reason": "Cancelled before 12 PM cutoff"
  }
  ```
* **DB Write**: `UPDATE customer_orders SET status = 'CANCELLED'` (Write - Global)

---

#### Tool 5: `relay_order_ready_to_driver_tool`
* **Action Source**: Action 6 (`relay_order_ready_signal_to_driver`)
* **Purpose**: Triggered when a chef marks an order packed ready. Finds assigned driver for that batch and notifies Driver Agent on WhatsApp.
* **Inputs**:
  - `chef_phone`: `str` (Required)
  - `order_id`: `str` (Required)
* **Expected Output Structure**:
  ```json
  {
    "order_id": "ord_104",
    "assigned_driver_phone": "+919988776655",
    "driver_notified": true
  }
  ```
* **DB Write**: Global Read on `system_delivery_stops` & `INSERT INTO system_agent_logs`.

---

#### Tool 6: `relay_gate_delivery_completed_tool`
* **Action Source**: Action 7 (`relay_gate_delivery_completed_to_customer`)
* **Purpose**: Triggered when a driver completes apartment gate drop-off. Notifies all affected customers on WhatsApp (*"Food delivered to Security Guard!"*).
* **Inputs**:
  - `driver_phone`: `str` (Required)
  - `order_ids_list`: `list[str]` (Required)
  - `apartment_gate_name`: `str` (Required)
* **Expected Output Structure**:
  ```json
  {
    "order_ids_notified": ["ord_101", "ord_104"],
    "delivery_location": "My Home Bhooja Gate",
    "status": "CUSTOMERS_NOTIFIED"
  }
  ```
* **DB Write**: `UPDATE customer_orders SET status = 'DELIVERED'` & Outbound Queue.

---

#### Tool 7: `relay_unlocatable_address_request_tool`
* **Action Source**: Action 8 (`relay_unlocatable_address_to_customer`)
* **Purpose**: Triggered when a driver reports an address not found. Prompts Customer Agent to ask customer for an updated WhatsApp location pin.
* **Inputs**:
  - `driver_phone`: `str` (Required)
  - `order_id`: `str` (Required)
* **Expected Output Structure**:
  ```json
  {
    "order_id": "ord_104",
    "customer_notified": true,
    "status": "WAITING_FOR_CUSTOMER_LOCATION_PIN"
  }
  ```
* **DB Write**: Updates LangGraph State Checkpointer.

---

#### Tool 8: `relay_traffic_delay_alert_tool`
* **Action Source**: Action 9 (`relay_traffic_delay_alert_to_customers`)
* **Purpose**: Triggered when a driver reports a traffic delay. Recalculates ETAs for remaining stops and alerts affected customers on WhatsApp.
* **Inputs**:
  - `driver_phone`: `str` (Required)
  - `route_id`: `str` (Required)
  - `delay_minutes`: `int` (Required)
  - `delay_reason`: `str` (Required)
* **Expected Output Structure**:
  ```json
  {
    "route_id": "rt_801",
    "delay_minutes": 15,
    "affected_customers_notified_count": 5
  }
  ```
* **DB Write**: `UPDATE system_delivery_stops SET estimated_arrival = ...`

---

### 💳 CATEGORY 4: Financial Webhooks & Order Confirmation

#### Tool 9: `process_payment_gateway_webhook_tool`
* **Action Source**: Action 10 (`process_payment_webhook_confirmation`)
* **Purpose**: Receives payment gateway webhooks (Razorpay/Stripe). If `status == 'PAID'`, transitions order status from `PENDING_PAYMENT` $\rightarrow$ `CONFIRMED`.
* **Inputs**:
  - `payment_id`: `str` (Required)
  - `transaction_id`: `str` (Required)
  - `status`: `str` (Required, `'PAID'` or `'FAILED'`)
* **Expected Output Structure**:
  ```json
  {
    "payment_id": "pay_901",
    "order_id": "ord_104",
    "order_status": "CONFIRMED",
    "payment_status": "PAID",
    "message": "Payment verified. Order status updated to CONFIRMED."
  }
  ```
* **DB Write**: `UPDATE customer_payments` & `UPDATE customer_orders SET status = 'CONFIRMED'` (Write - Global)

---

### 🔐 CATEGORY 5: Write Delegation & Audit Trail Logging

#### Tool 10: `delegate_cross_domain_write_tool`
* **Action Source**: Action 11 (`execute_cross_domain_write_delegation`)
* **Purpose**: Enforces least-privilege write boundaries. When a subagent requests a cross-domain write, Master Agent logs the request and delegates it to the target domain subagent.
* **Inputs**:
  - `requesting_role`: `str` (Required, `'CUSTOMER'` / `'CHEF'` / `'DRIVER'`)
  - `target_role`: `str` (Required, `'CUSTOMER'` / `'CHEF'` / `'DRIVER'`)
  - `target_table`: `str` (Required)
  - `payload`: `dict` (Required)
* **Expected Output Structure**:
  ```json
  {
    "delegation_id": "del_301",
    "requesting_role": "CUSTOMER",
    "target_role": "CHEF",
    "status": "DELEGATED_SUCCESSFULLY",
    "execution_result": "SUCCESS"
  }
  ```
* **DB Write**: `INSERT INTO system_agent_logs ...` (Write)

---

#### Tool 11: `dispatch_whatsapp_outbound_message_tool`
* **Action Source**: Action 12 (`enqueue_and_dispatch_whatsapp_message`)
* **Purpose**: Pushes outbound WhatsApp messages to `system_outbound_queue` and triggers Meta WhatsApp Business API gateway.
* **Inputs**:
  - `recipient_phone`: `str` (Required)
  - `message_text`: `str` (Required)
  - `recipient_role`: `str` (Required, `'CUSTOMER'` / `'CHEF'` / `'DRIVER'`)
* **Expected Output Structure**:
  ```json
  {
    "message_id": "msg_701",
    "recipient_phone": "+919876543210",
    "status": "QUEUED_AND_DISPATCHED"
  }
  ```
* **DB Write**: `INSERT INTO system_outbound_queue ...` (Write)

---

#### Tool 12: `log_system_audit_event_tool`
* **Action Source**: Action 13 (`log_system_audit_event`)
* **Purpose**: Writes immutable audit log for system events, cutoff locks, relays, and exceptions in `system_agent_logs`.
* **Inputs**:
  - `event_type`: `str` (Required)
  - `source_role`: `str` (Required)
  - `payload`: `dict` (Required)
  - `severity`: `str` (Required, `'INFO'` / `'WARNING'` / `'CRITICAL'`)
* **Expected Output Structure**:
  ```json
  {
    "audit_id": "aud_901",
    "event_type": "CUTOFF_LOCK",
    "status": "LOGGED_SUCCESSFULLY"
  }
  ```
* **DB Write**: `INSERT INTO system_agent_logs ...` (Write)
