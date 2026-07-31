# ⚙️ Master Specification: The 21 Write Executors (Guard 3)

This document contains the 100% complete specification, architecture, implementation map, and testing suite for all **21 Write Executors** in Homaatri — enforcing **Data Integrity Guard 3 (Single-Owner Write Invariant)** across PostgreSQL 16.

---

## 🧭 Architectural Purpose & Guard 3 Invariant

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                      DATA INTEGRITY GUARD 3 INVARIANT                       │
├─────────────────────────────────────────────────────────────────────────────┤
│ 1. NO DIRECT DATABASE WRITES INSIDE LLM TOOLS                                │
│    LLM Agent tools DO NOT execute raw SQL INSERT / UPDATE statements.      │
│ 2. SINGLE DOMAIN OWNERSHIP                                                  │
│    Every table in the 24-table database is written by EXACTLY ONE executor.  │
│ 3. CROSS-DOMAIN DELEGATION (DW1 & DW2)                                      │
│    Cross-domain status updates MUST delegate through Customer DW1 and DW2. │
│ 4. ATOMIC TRANSACTIONS (GUARD 1)                                            │
│    All executors run inside `async with transaction()` context managers.   │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 🗄️ Master Map of All 21 Write Executors

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           THE 21 WRITE EXECUTORS                            │
├───────────────────┬───────────────────┬───────────────────┬─────────────────┤
│ Chef Domain (3)   │ Customer (7)      │ Driver Domain (3) │ Master (8)      │
│ - Capacity Upsert │ - Reg & Location  │ - Driver Onboard  │ - Window Lock   │
│ - Stock Toggle    │ - Order Header    │ - Trip Init       │ - Cutoff Batch  │
│ - Food Readiness  │ - Add Line Item   │ - Trip Phase Upd  │ - Stop Update   │
│                   │ - Payment Record  │                   │ - HITL Session  │
│                   │ - Submit Review   │                   │ - Webhook Log   │
│                   │ - DW1 Status Tr   │                   │ - WA Queue      │
│                   │ - DW2 Payment Tr  │                   │ - Unified Msg   │
│                   │                   │                   │ - System Audit  │
└───────────────────┴───────────────────┴───────────────────┴─────────────────┘
```

---

## 🧑‍🍳 Category 1: Chef Domain Executors (`app/executors/chef.py`)

| # | Executor Function Name | Target Table | Primary Key | Description & Guard Rules |
|---|---|---|---|---|
| 1 | **`execute_daily_capacity_upsert()`** | `chef_daily_inventory` | `inv_...` | Idempotent upsert for dish daily prep caps per `(chef_phone, menu_item_id, service_date, meal_window)`. |
| 2 | **`execute_dish_stock_toggle()`** | `chef_menu_items` | `itm_...` | Flips dish availability (`is_available = True/False`). Raises `ValueError` if dish not found. |
| 3 | **`execute_order_readiness_record()`** | `chef_order_readiness` | `red_...` | Records dish packed status (`PACKED_READY`), box count, and special packing notes with timestamp. |

---

## 🙋‍♂️ Category 2: Customer Domain Executors & Delegated DW1/DW2 (`app/executors/customer.py`)

| # | Executor Function Name | Target Table | Primary Key | Description & Guard Rules |
|---|---|---|---|---|
| 4 | **`execute_customer_registration_and_location()`** | `customer_profiles` | `customer_phone` | Onboards new customer or updates GPS coordinates (`latitude`, `longitude`, `delivery_address`). Sets `is_registered = True`. |
| 5 | **`execute_customer_order_initialization()`** | `customer_orders` | `ord_...` | Creates new order header in `PENDING_PAYMENT` status with immutable `kitchen_name` snapshot. Assert customer exists. |
| 6 | **`execute_add_item_to_order()`** | `customer_order_items` | `ori_...` | Adds or updates line item quantity; recalculates order `cart_subtotal` and `total_amount` atomically. |
| 7 | **`execute_payment_record_creation()`** | `customer_payments` | `pay_...` | Creates payment record in `PENDING` status with amount due and gateway metadata. |
| 8 | **`execute_submit_order_review()`** | `customer_reviews` | `rev_...` | Submits post-delivery review. Guard 2 Asserts: `1 <= rating <= 5` and order status is `DELIVERED`. |
| 9 | **⭐ DW1: `execute_order_status_transition()`** | `customer_orders` | `ord_...` | **Delegated Executor 1**: Centralized single owner for order status transitions (`PENDING_PAYMENT` $\rightarrow$ `CONFIRMED` $\rightarrow$ `BATCHED` $\rightarrow$ `COOKING` $\rightarrow$ `PACKED` $\rightarrow$ `PICKED_UP` $\rightarrow$ `DELIVERED` / `CANCELLED`). Idempotent when `current == target`. |
| 10 | **⭐ DW2: `execute_payment_status_update()`** | `customer_payments` | `pay_...` | **Delegated Executor 2**: Updates payment status (`PAID`, `FAILED`, `REFUNDED`). When `PAID`, automatically cascades to **DW1** to mark order `CONFIRMED`. |

---

## 🚴‍♂️ Category 3: Driver Domain Executors (`app/executors/driver.py`)

| # | Executor Function Name | Target Table | Primary Key | Description & Guard Rules |
|---|---|---|---|---|
| 11 | **`execute_driver_profile_upsert()`** | `driver_profiles` | `driver_phone` | Onboards driver profile or updates vehicle plate, model, license, and on-shift status. |
| 12 | **`execute_driver_trip_initialization()`** | `driver_trip_status` | `trp_...` | Creates driver trip status ledger for assigned route; links `driver.current_assigned_route_id`. Assert driver active. |
| 13 | **`execute_driver_trip_phase_update()`** | `driver_trip_status` | `trp_...` | Single owner for trip execution phase updates (`ASSIGNED` $\rightarrow$ `EN_ROUTE_PICKUP` $\rightarrow$ `AT_KITCHEN` $\rightarrow$ `EN_ROUTE_DELIVERY` $\rightarrow$ `AT_GATE` $\rightarrow$ `COMPLETED`). Stamps `trip_started_at` & `trip_completed_at`. |

---

## 👑 Category 4: Master / System & Shared Runtime Executors (`app/executors/master.py`)

| # | Executor Function Name | Target Table | Primary Key | Description & Guard Rules |
|---|---|---|---|---|
| 14 | **`execute_meal_window_lock_and_creation()`** | `system_meal_windows` | `win_...` | Manages meal window lifecycle (`OPEN` $\rightarrow$ `LOCKED_PROCESSING` $\rightarrow$ `COMPLETED`). |
| 15 | **`execute_cutoff_batch_lock_and_routes_creation()`** | `system_delivery_routes` | `rt_...` | Locks cutoff window, creates GCP delivery route, stops, stop-orders junction records, and delegates to **DW1** to set order status to `BATCHED`. |
| 16 | **`execute_stop_status_update()`** | `system_delivery_stops` | `stp_...` | Single owner for stop status updates (`PENDING` $\rightarrow$ `ARRIVED` $\rightarrow$ `COMPLETED`). Stamps `actual_arrival`. |
| 17 | **`execute_hitl_session_create_or_resume()`** | `system_hitl_sessions` | `hitl_...` | Creates or resumes 15-minute TTL HITL sessions (`WAITING` $\rightarrow$ `RESUMED` / `EXPIRED` / `RESOLVED`). Calculates `expires_at`. |
| 18 | **`execute_payment_webhook_idempotency_log()`** | `system_payment_webhook_events` | `evt_...` | Idempotency logger for payment gateway webhooks. Checks `gateway_event_id` uniqueness before processing. |
| 19 | **`execute_outbound_whatsapp_enqueue()`** | `system_outbound_queue` | `out_...` | Enqueues outbound WhatsApp notifications into background queue (`status = QUEUED`). |
| 20 | **`execute_conversation_message_insert()`** | `conversation_messages` | `msg_...` | **Shared Runtime Chat Log**: Insert-only ledger for all inbound and outbound WhatsApp messages read by the Context Assembler. |
| 21 | **`execute_system_audit_log()`** | `system_agent_logs` | `log_...` | Records operational audit events and system errors in audit log. |

---

## 🧪 Integration Testing & Verification Suite

All 21 executors were verified using automated async integration tests in `tests/` against a live **PostgreSQL 16 container** running on port `5432`:

```bash
============================= test session starts ==============================
platform linux -- Python 3.10.12, pytest-8.3.4, pluggy-1.5.0
rootdir: /home/dinesh/coding/PROJECTS/homatri
configfile: pyproject.toml
testpaths: tests
plugins: langsmith-0.3.45, Faker-30.8.1, anyio-4.8.0, asyncio-0.25.2
asyncio: mode=auto, asyncio_default_fixture_loop_scope=function
collecting ... collected 16 items

tests/test_chef_executors.py ....                                        [ 25%]
tests/test_customer_executors.py .....                                   [ 56%]
tests/test_driver_executors.py ..                                        [ 68%]
tests/test_master_executors.py .....                                     [100%]

============================= 16 passed in 26.53s ==============================
```
