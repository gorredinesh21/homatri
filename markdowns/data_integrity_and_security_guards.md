# 🛡️ Master Specification: Data Integrity, Prefixed UUID Standard & Security Guards

This document outlines the complete architectural specifications for **Data Integrity, Prefixed Primary Keys, Pre-Condition Assertions, and Write Executors** across the Homaatri platform.

---

## 🆔 1. Prefixed UUID Primary Key Standard

To guarantee 100% security, prevent ID enumeration attacks (`/order/1`, `/order/2`), and eliminate LLM / developer entity confusion, all tables follow a standardized Primary Key rule:

### Category A: Natural Phone Primary Keys (3 Core Human Actor Registries)
* **`chef_profiles`** $\rightarrow$ `chef_phone` (`VARCHAR(15)` - e.g. `'9876543210'`)
* **`customer_profiles`** $\rightarrow$ `customer_phone` (`VARCHAR(15)` - e.g. `'9123456789'`)
* **`driver_profiles`** $\rightarrow$ `driver_phone` (`VARCHAR(15)` - e.g. `'9988776655'`)

### Category B: Prefixed UUID Primary Keys (All Transactional, System & Audit Tables)
Format: `VARCHAR(36)` with a 3-letter entity prefix + short UUID (e.g. `ord_a1b2c3d4e5f6`).

| Entity Domain | Table Name | Primary Key Column | Standardized ID Format | Real Example ID |
| :--- | :--- | :--- | :--- | :--- |
| **Chef** | `chef_profiles` | `chef_phone` | Normalized 10-digit Phone | `'9876543210'` |
| **Chef** | `chef_menu_items` | `menu_item_id` | `itm_` + UUID | `'itm_a1b2c3d4e5f6'` |
| **Chef** | `chef_daily_inventory` | `inventory_id` | `inv_` + UUID | `'inv_9f8e7d6c5b4a'` |
| **Chef** | `chef_order_readiness` | `readiness_id` | `red_` + UUID | `'red_1a2b3c4d5e6f'` |
| **Customer** | `customer_profiles` | `customer_phone` | Normalized 10-digit Phone | `'9123456789'` |
| **Customer** | `customer_orders` | `order_id` | `ord_` + UUID | `'ord_7890abcdef12'` |
| **Customer** | `customer_order_items` | `item_id` | `ori_` + UUID | `'ori_3456abcdef78'` |
| **Customer** | `customer_payments` | `payment_id` | `pay_` + UUID | `'pay_9012abcdef34'` |
| **Customer** | `customer_reviews` | `review_id` | `rev_` + UUID | `'rev_5678abcdef90'` |
| **Driver** | `driver_profiles` | `driver_phone` | Normalized 10-digit Phone | `'9988776655'` |
| **Driver** | `driver_trip_status` | `trip_id` | `trp_` + UUID | `'trp_1234abcdef56'` |
| **Master** | `system_meal_windows` | `window_id` | `win_` + UUID | `'win_20260731_lunch'` |
| **Master** | `system_settings` | `key` | Natural Key String | `'delivery_fee'` |
| **Master** | `system_delivery_routes` | `route_id` | `rt_` + UUID | `'rt_801abcdef123'` |
| **Master** | `system_delivery_stops` | `stop_id` | `stp_` + UUID | `'stp_456abcdef789'` |
| **Master** | `system_agent_logs` | `log_id` | `log_` + UUID | `'log_101abcdef234'` |
| **Master** | `system_outbound_queue` | `message_id` | `out_` + UUID | `'out_202abcdef345'` |
| **Master** | `system_hitl_sessions` | `session_id` | `hitl_` + UUID | `'hitl_303abcdef456'` |
| **Master** | `system_payment_webhook_events`| `event_id` | `evt_` + UUID | `'evt_404abcdef567'` |
| **Master** | `system_route_optimization_runs`| `run_id` | `run_` + UUID | `'run_505abcdef678'` |
| **Shared** | `conversation_messages` | `message_id` | `msg_` + UUID | `'msg_606abcdef789'` |
| **Admin** | `admin_users` | `admin_id` | `adm_` + UUID | `'adm_707abcdef890'` |
| **Admin** | `admin_activity_log` | `activity_id` | `act_` + UUID | `'act_808abcdef901'` |
| **Admin** | `admin_ai_queries` | `query_id` | `aiq_` + UUID | `'aiq_909abcdef012'` |

---

## 🛡️ 2. The 4 Programmatic Integrity & Security Guards

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                       PROGRAMMATIC DATA INTEGRITY GUARDS                    │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │
 ┌─────────────────┬─────────────────┬─┴───────────────┬─────────────────┐
 ▼                 ▼                 ▼                 ▼                 ▼
GUARD 1: ATOMIC    GUARD 2: HARD     GUARD 3: DELEGATED GUARD 4: AUTOMATED
SQL TRANSACTIONS   PRE-CONDITIONS    WRITE EXECUTORS   PYTEST SUITE
All-or-Nothing     Assert Guards     Single Owner      Verify 40 Tools
Rollbacks          Before Writes     Status Functions  on Every Commit
```

---

### GUARD 1: Atomic Database Transactions (`all-or-nothing` rollbacks)
* Every multi-table write inside a tool executes within a single `async with db.transaction():` context block.
* If any single step fails or encounters an exception, PostgreSQL automatically rolls back 100% of all changes, preventing partial or corrupted table states.

---

### GUARD 2: Hard Python Pre-Condition Assertions (Matrix across 40 Tools)

#### 👨‍🍳 Chef Agent (9 Tools)
* **`get_chef_profile_tool`**: Assert `chef_phone` valid E.164 format; assert profile exists and `active_status == true`.
* **`set_daily_dish_capacity_tool`**: Assert `chef_phone` exists; assert `menu_item_id` exists; assert `menu_item.chef_phone == chef_phone` (Ownership); assert `max_capacity > 0`; assert `service_date >= CURRENT_DATE`.
* **`toggle_dish_stock_tool`**: Assert `menu_item_id` exists; assert `menu_item.chef_phone == chef_phone` (Ownership).
* **`check_daily_inventory_status_tool`**: Assert `chef_phone` exists.
* **`get_chef_batch_checklist_tool`**: Assert `chef_phone` exists; assert `meal_window` IN (`'LUNCH'`, `'DINNER'`).
* **`mark_order_packed_ready_tool`**: Assert `order_id` exists; assert `order.chef_phone == chef_phone` (Ownership); assert `order.status` IN (`'CONFIRMED'`, `'BATCHED'`, `'COOKING'`).
* **`get_assigned_driver_info_tool`**: Assert `chef_phone` exists; assert `meal_window` IN (`'LUNCH'`, `'DINNER'`).
* **`respond_to_custom_request_tool`**: Assert `order_id` exists; assert `order.chef_phone == chef_phone` (Ownership); assert `system_hitl_session` exists with `status == 'WAITING'` and `waiting_on_role == 'CHEF'`; assert `decision` IN (`'ACCEPTED'`, `'DECLINED'`, `'COUNTER_OFFER'`).
* **`check_driver_arrival_status_tool`**: Assert `chef_phone` exists.

#### 🙋‍♂️ Customer Agent (11 Tools)
* **`get_customer_profile_tool`**: Assert `customer_phone` valid E.164 format.
* **`register_customer_profile_tool`**: Assert `name` non-empty; assert `delivery_address` non-empty.
* **`update_customer_location_pin_tool`**: Assert `customer_phone` exists; assert `latitude` between -90 and 90; assert `longitude` between -180 and 180.
* **`find_nearby_home_kitchens_tool`**: Assert `customer_phone` exists; assert `customer.is_registered == true` (has shared location pin); assert `meal_window` IN (`'LUNCH'`, `'DINNER'`).
* **`view_chef_menu_tool`**: Assert `chef_phone` exists; assert `chef.active_status == true`.
* **`initialize_customer_order_tool`**: Assert `customer.is_registered == true`; assert `chef.active_status == true`; assert `system_meal_windows.status == 'OPEN'` (Before 12 PM / 7 PM cutoff); assert `items` list non-empty; for each item assert `quantity <= remaining_inventory`.
* **`add_item_to_order_tool`**: Assert `order_id` exists; assert `order.customer_phone == customer_phone` (Ownership); assert `order.status` IN (`'DRAFT_CART'`, `'PENDING_PAYMENT'`); assert `system_meal_windows.status == 'OPEN'`; assert `quantity <= remaining_inventory`.
* **`generate_payment_link_tool`**: Assert `order_id` exists; assert `order.customer_phone == customer_phone` (Ownership); assert `order.status` IN (`'PENDING_PAYMENT'`, `'CONFIRMED'`); assert `order.cart_subtotal > 0`.
* **`get_active_order_status_tool`**: Assert `customer_phone` exists.
* **`get_order_history_tool`**: Assert `customer_phone` exists.
* **`submit_order_review_tool`**: Assert `order_id` exists; assert `order.customer_phone == customer_phone` (Ownership); assert `order.status == 'DELIVERED'`; assert `chef_rating` between 1 and 5; assert no review row already exists for this `order_id`.

#### 👑 Master Agent (12 Tools)
* **`validate_meal_cutoff_clock_tool`**: Assert `meal_window` IN (`'LUNCH'`, `'DINNER'`).
* **`execute_cutoff_batch_..._tool`**: Assert `system_meal_windows.status == 'OPEN'`; assert `CURRENT_TIMESTAMP >= cutoff_at`; assert count of confirmed orders > 0.
* **`relay_dietary_request_to_chef_tool`**: Assert `order_id` exists; assert `order.customer_phone == customer_phone` (Ownership); assert `order.status` IN (`'CONFIRMED'`, `'BATCHED'`, `'COOKING'`).
* **`process_order_cancellation_tool`**: Assert `order_id` exists; assert `order.customer_phone == customer_phone` (Ownership); assert `order.status` NOT IN (`'DELIVERED'`, `'CANCELLED'`).
* **`relay_order_ready_to_driver_tool`**: Assert `order_id` exists; assert `order.status == 'PACKED'`; assert assigned driver exists.
* **`relay_gate_delivery_completed_tool`**: Assert `stop_id` exists; assert `stop.stop_type == 'DROPOFF_GATE'`; assert `driver_phone` matches route driver.
* **`relay_unlocatable_address_..._tool`**: Assert `order_id` exists; assert `driver_phone` matches assigned driver.
* **`relay_traffic_delay_alert_tool`**: Assert `route_id` exists; assert `route.driver_phone == driver_phone` (Ownership); assert `delay_minutes > 0`.
* **`process_payment_webhook_tool`**: Assert `signature_verified == true`; assert `gateway_event_id` does NOT already exist in `system_payment_webhook_events` (Idempotency); assert `payment_id` exists.
* **`delegate_cross_domain_write_tool`**: Assert `requesting_role` valid; assert `target_role` valid; assert `target_table` valid.
* **`dispatch_whatsapp_outbound_tool`**: Assert `recipient_phone` format valid; assert `message_text` non-empty.
* **`log_system_audit_event_tool`**: Assert `event_type` non-empty; assert `severity` IN (`'INFO'`, `'WARNING'`, `'CRITICAL'`).

#### 🚴‍♂️ Delivery Driver Agent (8 Tools)
* **`get_driver_profile_tool`**: Assert `driver_phone` valid E.164 format.
* **`get_assigned_route_itinerary_tool`**: Assert `driver_phone` exists; assert `driver.active_status == true`.
* **`dispatch_next_leg_navigation_tool`**: Assert `driver_phone` matches assigned route driver; assert `current_stop_index < total_stops`.
* **`mark_driver_reached_stop_tool`**: Assert `driver_phone` matches assigned route driver; assert `stop_index == driver_trip_status.current_stop_index`; assert `stop.status == 'PENDING'`.
* **`mark_orders_picked_up_tool`**: Assert `driver_phone` matches route driver; assert `stop.stop_type == 'PICKUP_KITCHEN'`; assert `stop.status == 'ARRIVED'`; for each order assert `order.status` IN (`'PACKED'`, `'COOKING'`, `'BATCHED'`).
* **`mark_gate_delivery_completed_tool`**: Assert `driver_phone` matches route driver; assert `stop.stop_type == 'DROPOFF_GATE'`; assert `stop.status == 'ARRIVED'`; for each order assert `order.status == 'PICKED_UP'`.
* **`report_unlocatable_address_tool`**: Assert `order_id` exists on current stop; assert `driver_phone` matches route driver.
* **`report_vehicle_delay_alert_tool`**: Assert driver has active trip in progress (`driver_trip_status.status == 'IN_PROGRESS'`); assert `delay_minutes > 0`.

---

## 🗺️ 3. GUARD 3: Master Write Executor Map (21 Write Executors)

### Chef Domain (3 Executors)
1. `execute_daily_capacity_upsert()` $\rightarrow$ `chef_daily_inventory`
2. `execute_dish_stock_toggle()` $\rightarrow$ `chef_menu_items`
3. `execute_order_readiness_record()` $\rightarrow$ `chef_order_readiness`

### Customer Domain (7 Executors)
4. `execute_customer_registration_and_location()` $\rightarrow$ `customer_profiles`
5. `execute_customer_order_initialization()` $\rightarrow$ `customer_orders`, `customer_order_items`
6. `execute_add_item_to_order()` $\rightarrow$ `customer_order_items`
7. `execute_payment_record_creation()` $\rightarrow$ `customer_payments`
8. `execute_submit_order_review()` $\rightarrow$ `customer_reviews`
9. **`execute_order_status_transition()` (DW1)** $\rightarrow$ `customer_orders` (Single owner for all order status changes: `CONFIRMED`, `BATCHED`, `PACKED`, `PICKED_UP`, `DELIVERED`, `CANCELLED`).
10. **`execute_payment_status_update()` (DW2)** $\rightarrow$ `customer_payments` (Single owner for payment updates: `PAID`, `FAILED`, `REFUNDED`).

### Driver Domain (1 Executor)
11. `execute_driver_trip_phase_update()` $\rightarrow$ `driver_trip_status`

### Master / System Domain (7 Executors)
12. `execute_cutoff_batch_lock_and_routes_creation()` $\rightarrow$ `system_meal_windows`, `system_delivery_routes`, `system_delivery_stops`, `system_delivery_stop_orders`, `system_route_optimization_runs`
13. `execute_hitl_session_create_or_resume()` $\rightarrow$ `system_hitl_sessions`
14. `execute_stop_status_update()` $\rightarrow$ `system_delivery_stops`
15. `execute_stop_eta_recalculation()` $\rightarrow$ `system_delivery_stops`
16. `execute_payment_webhook_idempotency_log()` $\rightarrow$ `system_payment_webhook_events`
17. `execute_outbound_whatsapp_enqueue()` $\rightarrow$ `system_outbound_queue`
18. `execute_system_audit_log()` $\rightarrow$ `system_agent_logs`

### Shared Runtime & Admin (3 Executors)
19. `execute_conversation_message_insert()` $\rightarrow$ `conversation_messages` (Shared Messaging Runtime)
20. `execute_admin_profile_onboarding()` $\rightarrow$ `chef_profiles`, `chef_menu_items`, `driver_profiles`, `admin_users`
21. `execute_admin_activity_log()` $\rightarrow$ `admin_activity_log`

---

## 🧪 4. GUARD 4: Automated `pytest` Integration Test Suite

* Organized into 4 test modules: `test_chef_tools.py`, `test_customer_tools.py`, `test_master_tools.py`, `test_driver_tools.py`.
* Every tool is tested against 2 mandatory scenarios:
  1. **Happy Path**: Valid inputs $\rightarrow$ Verifies exact row insertions/updates in test PostgreSQL database.
  2. **Pre-Condition Failure**: Invalid inputs / illegal status jumps $\rightarrow$ Verifies Guard 2 assertion raises `AssertionError` and Guard 1 rolls back 100%.
