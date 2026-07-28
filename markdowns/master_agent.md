# 👑 Master Specification: The Master Agent

This document outlines the complete persona, communication rules, categories, toolset, and guardrails for the **Master Agent** in Homaatri.

---

## 🎭 1. Persona & Communication Rules
* **Human Persona**: Highly disciplined, objective, structured, and authoritative. Acts like the Chief Operating Officer (COO) + General Manager + Security Officer.
* **Core Rule**: Has **Global Write Authority** across all 18 tables. Enforces all system policies, validates subagent delegation requests, logs every cross-domain action, and controls the cutoff clock.

---

## 🛠️ 2. Master Agent Action & Toolset (13 Actions)

### ⏱️ CATEGORY 1: Cutoff Clock & Batch State Guardrails

#### 1. `validate_meal_window_cutoff_clock(meal_window, current_time)`
* **Purpose**: Called before any order creation. Rejects Lunch orders after 12:00 PM and Dinner orders after 7:00 PM.
* **Inputs**: `meal_window: str` (`'LUNCH'` or `'DINNER'`), `current_time: timestamp`
* **Outputs**: `dict` (`is_allowed: bool`, cutoff status message).
* **DB Operation**: `SELECT * FROM system_meal_windows WHERE ...` (Read)

#### 2. `lock_meal_window_batch(meal_window, date)`
* **Purpose**: Executed automatically at 12:00 PM & 7:00 PM. Locks meal window status from `OPEN` $\rightarrow$ `LOCKED_PROCESSING`, freezing new customer orders for that batch.
* **Inputs**: `meal_window: str`, `date: str`
* **Outputs**: `dict` (Batch locked confirmation, total confirmed orders count).
* **DB Operation**: `UPDATE system_meal_windows SET status = 'LOCKED_PROCESSING' WHERE ...` (Write - `system_*`)

---

### 🗺️ CATEGORY 2: GCP Route Optimization Engine

#### 3. `orchestrate_gcp_route_optimization(meal_window_id, date)`
* **Purpose**: Called at cutoff time. Gathers all Chef Kitchen coordinates & Customer Delivery coordinates, merges orders with identical apartment gate coordinates into single drop-off stops, calls GCP Route Optimization API (ONCE), and saves the master sequenced itinerary (`system_delivery_stops`).
* **Inputs**: `meal_window_id: str`, `date: str`
* **Outputs**: `dict` (Master trip route created, driver assigned, sequenced stops count).
* **DB Operation**: `INSERT INTO system_delivery_routes` & `INSERT INTO system_delivery_stops` (Write - `system_*`)

---

### 🔄 CATEGORY 3: Inter-Agent Cross-Domain Message Relays (Mediator Duty)

#### 4. `relay_customer_dietary_request_to_chef(order_id, dietary_notes)`
* **Support For**: Customer Agent $\rightarrow$ Chef Agent.
* **Purpose**: Takes customer's mid-cooking custom request (*"No garlic for Order #104"*), verifies order status, and triggers LangGraph `interrupt()` on Chef Agent to prompt the real Chef on WhatsApp.
* **Inputs**: `order_id: str`, `dietary_notes: str`
* **Outputs**: `dict` (Relay status; Chef Agent interrupted for approval).
* **DB Operation**: `INSERT INTO system_agent_logs ...` (Write - `system_*`)

#### 5. `process_order_cancellation_request(order_id, customer_id, reason)`
* **Support For**: Customer Agent $\rightarrow$ System.
* **Purpose**: Evaluates customer cancellation request against strict platform rules (e.g. if order is before cutoff $\rightarrow$ auto-cancel & refund; if after cutoff & cooking started $\rightarrow$ reject cancellation).
* **Inputs**: `order_id: str`, `customer_id: str`, `reason: str`
* **Outputs**: `dict` (Cancellation status, refund eligibility).
* **DB Operation**: `UPDATE customer_orders SET status = 'CANCELLED'` (Write - Global)

#### 6. `relay_order_ready_signal_to_driver(order_id, chef_id)`
* **Support For**: Chef Agent $\rightarrow$ Driver Agent.
* **Purpose**: When Chef marks an order packed, Master Agent finds the assigned driver for that batch and notifies Driver Agent.
* **Inputs**: `order_id: str`, `chef_id: str`
* **Outputs**: `dict` (Driver notified confirmation).
* **DB Operation**: Global Read on `system_delivery_stops` & `INSERT INTO system_agent_logs`.

#### 7. `relay_gate_delivery_completed_to_customer(order_ids_list, driver_name)`
* **Support For**: Driver Agent $\rightarrow$ Customer Agent.
* **Purpose**: When driver completes apartment gate delivery, Master Agent triggers Customer Agent to message affected customers on WhatsApp (*"Your food has been delivered to your Apartment Security Gate!"*).
* **Inputs**: `order_ids_list: list[str]`, `driver_name: str`
* **Outputs**: `dict` (Customer WhatsApp messages dispatched).
* **DB Operation**: `UPDATE customer_orders SET status = 'DELIVERED'` & Outbound Queue.

#### 8. `relay_unlocatable_address_to_customer(order_id, driver_name)`
* **Support For**: Driver Agent $\rightarrow$ Customer Agent.
* **Purpose**: When driver reports unlocatable address, Master Agent notifies Customer Agent to ask customer for an updated WhatsApp location pin.
* **Inputs**: `order_id: str`, `driver_name: str`
* **Outputs**: `dict` (Customer location pin request sent).
* **DB Operation**: Updates LangGraph state checkpointer.

#### 9. `relay_traffic_delay_alert_to_customers(route_id, delay_minutes)`
* **Support For**: Driver Agent $\rightarrow$ Customer Agent.
* **Purpose**: When driver reports traffic delay, Master Agent recalculates ETAs for remaining stops and notifies affected customers.
* **Inputs**: `route_id: str`, `delay_minutes: int`
* **Outputs**: `dict` (Updated ETAs across delivery stops).
* **DB Operation**: `UPDATE system_delivery_stops SET estimated_arrival = ...`

---

### 💳 CATEGORY 4: Financial Webhooks & Order Confirmation

#### 10. `process_payment_webhook_confirmation(payment_id, transaction_id, status)`
* **Purpose**: Receives payment gateway webhook (Razorpay/Stripe). If `status == 'PAID'`, transitions order from `PENDING_PAYMENT` $\rightarrow$ `CONFIRMED`.
* **Inputs**: `payment_id: str`, `transaction_id: str`, `status: str`
* **Outputs**: `dict` (Payment recorded, order status updated to `CONFIRMED`).
* **DB Operation**: `UPDATE customer_payments` & `UPDATE customer_orders SET status = 'CONFIRMED'` (Write - Global)

---

### 🔐 CATEGORY 5: Write Delegation & Audit Trail Logging

#### 11. `execute_cross_domain_write_delegation(requesting_agent, target_agent, target_table, write_payload)`
* **Purpose**: Enforces Master Agent's write-delegation protocol. When a subagent requests a cross-domain write, Master Agent delegates it to the target domain subagent after logging.
* **Inputs**: `requesting_agent: str`, `target_agent: str`, `target_table: str`, `write_payload: dict`
* **Outputs**: `dict` (Delegation status, execution result).
* **DB Operation**: `INSERT INTO system_agent_logs ...` (Write - `system_*`)

#### 12. `enqueue_and_dispatch_whatsapp_message(recipient_phone, message_text, role)`
* **Purpose**: Pushes outbound WhatsApp messages to `system_outbound_queue` and calls Meta WhatsApp Business API gateway.
* **Inputs**: `recipient_phone: str`, `message_text: str`, `role: str`
* **Outputs**: `dict` (Message queued & dispatched).
* **DB Operation**: `INSERT INTO system_outbound_queue ...` (Write - `system_*`)

#### 13. `log_system_audit_event(event_type, source_role, payload, severity)`
* **Purpose**: Writes audit log for system events, cutoff locks, relays, and exceptions.
* **Inputs**: `event_type: str`, `source_role: str`, `payload: dict`, `severity: str`
* **Outputs**: `dict` (Audit log inserted).
* **DB Operation**: `INSERT INTO system_agent_logs ...` (Write - `system_*`)
