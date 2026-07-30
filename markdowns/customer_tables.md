# 🙋‍♂️ Customer Agent — Table Design & Query Access Map

This document is the **Milestone 1** table design for the **Customer domain**, derived bottom-up from the Customer Agent's tools using the query-inventory method.

> **Tool count note:** the original spec listed 11 tools. Tool 3 (`update_customer_location_pin_tool`) was **folded into `register`** as a 2-phase HITL onboarding flow (write details → `interrupt()` for location → save coords), leaving **10 tools**.

---

## 🧭 Base Rules Applied (same for all agents)
1. **Phone is the natural key** (`customer_phone`); transactional rows (orders/items/payments/reviews) keep `VARCHAR(36)` surrogate ids.
2. **Write invariant** — a tool WRITES only `customer_*`; every cross-domain write is a HANDOFF to Master (note only).
3. **Reads are global** — cross-domain reads stay on the list, finalized under the owner.
4. **Master delegates cross-domain writes** to the owner domain's deterministic write-executor; writes INTO the customer domain land here as executors (see Delegated-Write Executors).
5. **`driver_locations` dropped** — live status from `system_delivery_stops`.
6. **Data types** — money `DECIMAL(10,2)`; lat `DECIMAL(10,8)`/lng `DECIMAL(11,8)`; timestamps `TIMESTAMPTZ`; FKs `ON DELETE RESTRICT`.

### Design decisions applied this pass
- **⚑A Cutoff = direct read** of `system_meal_windows` (no LLM hop to Master).
- **⚑B Payment = Master owns the gateway.** Customer computes the final amount → hands to Master → Master mints the UPI link. Two parallel LangGraph interrupts: Master waits on the provider webhook; Customer waits on Master's approval. Customer domain owns the `customer_payments` records (written via delegated executor).
- **⚑C `units_sold` DERIVED** = `SUM(customer_order_items.quantity)` for chef+dish+date → **no cross-domain write** on order creation, always consistent.
- **⚑D Snapshots** — `kitchen_name` on the order; `dish_name`, `unit_price`, `service_date` on the item, captured at order time (immutable; keeps receipts correct + avoids cross-domain joins).
- **⚑E Avg ratings derived** from `customer_reviews` via global read → no write to `chef_/driver_profiles`.

---

## 📋 Customer Agent — Query List

| Q | Tool | R/W | What it does | Table(s) | Filter/key cols | Index | On list? |
|---|---|---|---|---|---|---|---|
| Q1 | get_profile | R | identify customer (HOT) | `customer_profiles` | customer_phone | PK(customer_phone) | ✅ |
| Q2 | register *(phase 1)* | W | insert profile (name/address, `is_registered=false`) | `customer_profiles` | customer_phone | PK | ✅ own |
| — | register | PAUSE | `interrupt()` "share location"; if/else validates reply (checkpoint → `system_hitl_sessions`) | — | — | — | ⚑ note |
| Q3 | register *(phase 2, resume)* | W | save coords + `is_registered=true` | `customer_profiles` | customer_phone | PK | ✅ own |
| Q4 | find_nearby | R | get customer's coords | `customer_profiles` | customer_phone | PK | ✅ own |
| Q5 | find_nearby | R (x-domain) | active kitchens + availability for meal (Haversine in app; remaining derived) | `chef_profiles` ⋈ `chef_menu_items` ⋈ `chef_daily_inventory` | active_status; meal_type, is_available; service_date | chef_profiles(active_status); chef_menu_items(chef_phone,meal_type,is_available); chef_daily_inventory(chef_phone,service_date) | ✅ (Chef) |
| Q6 | view_menu | R (x-domain) | dishes + remaining inventory for a chef+meal | `chef_menu_items` ⋈ `chef_daily_inventory` (+ SUM `customer_order_items`) | chef_phone, meal_type | chef_menu_items(chef_phone,meal_type) | ✅ (Chef + own) |
| Q7 | init_order | R (x-domain) | is cutoff open? (direct read) | `system_meal_windows` (+`system_settings`) | service_date, meal_type | UNIQUE(service_date,meal_type) | ✅ (System) |
| Q8 | init_order | R (x-domain + own) | validate items: unit_price + dish_name + availability + remaining capacity (max_cap − SUM sold) | `chef_menu_items` ⋈ `chef_daily_inventory` (+ SUM `customer_order_items`) | menu_item_id; chef_phone, service_date | chef_menu_items(PK); chef_daily_inventory(chef_phone,menu_item_id,service_date) | ✅ (Chef + own) |
| Q9 | init_order | W | insert order header (snapshot `kitchen_name`, `PENDING_PAYMENT`) | `customer_orders` | order_id | PK | ✅ own |
| Q10 | init_order | W | insert line items (snapshot `dish_name`, `unit_price`, `service_date`) | `customer_order_items` | order_id | (order_id) | ✅ own |
| Q11 | add_item | R | order editable & belongs to customer (pre-cond) | `customer_orders` | order_id, customer_phone | PK | ✅ own |
| Q12 | add_item | R (x-domain + own) | validate item: price + remaining capacity (derived) | `chef_menu_items` ⋈ `chef_daily_inventory` (+ SUM items) | menu_item_id; chef_phone, service_date | as Q8 | ✅ (Chef + own) |
| Q13 | add_item | W | append line item (snapshot) | `customer_order_items` | order_id | (order_id) | ✅ own |
| Q14 | payment_link | R | read order+items → compute final amount | `customer_orders` ⋈ `customer_order_items` | order_id | PK; (order_id) | ✅ own |
| Q15 | payment_link | R (x-domain) | delivery_fee / config | `system_settings` | key | PK(key) | ✅ (System) |
| Q16 | payment_link | R | prior payments (initial vs top-up detect) | `customer_payments` | order_id | (order_id) | ✅ own |
| — | payment_link | HANDOFF | send final amount → Master mints UPI link via gateway → returns link | → Master (owns gateway) | — | — | ⚑ note |
| Q17 | payment_link | W | insert `customer_payments` (PENDING + link) — Customer executor, delegated by Master | `customer_payments` | order_id | (order_id) | ✅ own (delegated) |
| — | payment_link | PAUSE | Customer `interrupt()` `PAYMENT_AWAIT_MASTER_APPROVAL`; resumes on Master approval → notify user (checkpoint → `system_hitl_sessions`) | — | — | — | ⚑ note |
| Q18 | active_status | R | customer's active order | `customer_orders` | customer_phone, status | (customer_phone, status) | ✅ own |
| Q19 | active_status | R (x-domain) | live status: readiness + route + stop + driver | `chef_order_readiness` ⋈ `system_delivery_stops` ⋈ `system_delivery_routes` ⋈ `driver_profiles` | order_id; stop→route→driver | stops(target_ref_id,stop_type) | ✅ (Chef/System/Rider) |
| Q20 | order_history | R | past delivered orders + dishes (snapshots → no chef join) | `customer_orders` ⋈ `customer_order_items` | customer_phone, status=DELIVERED | (customer_phone, status) | ✅ own |
| Q21 | submit_review | R | verify order delivered & customer's (pre-cond) | `customer_orders` | order_id | PK | ✅ own |
| Q22 | submit_review | W | insert review | `customer_reviews` | order_id | (order_id) | ✅ own |

**Write check:** every direct `W` (Q2,Q3,Q9,Q10,Q13,Q22) writes `customer_*` only ✅. `units_sold` derived → no cross-domain write from order creation. Payment write (Q17) is a delegated executor.

---

## 🔁 Delegated-Write Executors (customer domain — triggered by Master)

All cross-domain writes INTO the customer domain execute here (Master delegates → Customer executor performs, with validation):

| DW | Executor | W | What it does | Table | Delegated by / trigger |
|---|---|---|---|---|---|
| DW1 | `execute_order_status_transition` | W | apply status change | `customer_orders` | Master ← cutoff (BATCHED), Chef (PACKED), Driver (PICKED_UP/DELIVERED), payment (CONFIRMED), cancel (CANCELLED) |
| DW2 | `execute_payment_status_update` | W | mark PAID/FAILED/REFUNDED | `customer_payments` | Master ← payment webhook |

*(Inbound payment webhook → Master verifies + dedups on `system_payment_webhook_events` → delegates DW1(CONFIRMED)+DW2(PAID) → approves the waiting Customer interrupt.)*

---

## 🗄️ Customer-Owned Tables (6)

| Table | Columns | Keys / Indexes |
|---|---|---|
| **`customer_profiles`** | customer_phone (PK), name, delivery_address, latitude `DECIMAL(10,8)`, longitude `DECIMAL(11,8)`, is_registered `bool`, created_at, updated_at | PK(customer_phone) |
| **`customer_orders`** | order_id (PK `VARCHAR(36)`), customer_phone (FK), chef_phone (FK→chef_profiles), meal_window `meal_window_enum`, service_date `DATE`, status `order_status_enum`, cart_subtotal, delivery_fee, total_amount `DECIMAL(10,2)`, kitchen_name (snapshot), created_at, updated_at | PK; (customer_phone, status); **(chef_phone, meal_window, service_date, status)** ← chef checklist |
| **`customer_order_items`** | item_id (PK), order_id (FK), menu_item_id (FK→chef_menu_items), chef_phone (denorm), service_date (snapshot), dish_name (snapshot), quantity `INT`, unit_price `DECIMAL(10,2)` (snapshot), item_subtotal, special_instructions `TEXT`, created_at | (order_id); **(chef_phone, menu_item_id, service_date)** ← derived units_sold; (menu_item_id) |
| **`customer_payments`** | payment_id (PK), order_id (FK), customer_phone (FK), payment_type `payment_type_enum`, amount_due `DECIMAL(10,2)`, payment_link_url, gateway, gateway_payment_id, transaction_id, status `payment_status_enum`, created_at, paid_at | (order_id); (customer_phone); (status) |
| **`customer_reviews`** | review_id (PK), order_id (FK), customer_phone (FK), chef_phone, driver_phone, chef_rating `INT`(1-5), driver_rating `INT`(1-5), review_text `TEXT`, created_at | (order_id); (chef_phone); (driver_phone) ← derive avg |
| ~~`customer_chat_history`~~ *(superseded)* | Replaced by the unified runtime-written **`conversation_messages`** (see [`master_tables.md`](master_tables.md)) | — |

**New enums introduced:** `order_status_enum` (PENDING_PAYMENT, CONFIRMED, BATCHED, COOKING, PACKED, PICKED_UP, DELIVERED, CANCELLED), `payment_status_enum` (PENDING, PAID, FAILED, REFUNDED), `payment_type_enum` (INITIAL, TOPUP, REFUND).

---

## 🔗 Recorded for Later (not on Customer list)
- **Cross-domain reads → owners:** `chef_profiles`/`chef_menu_items`/`chef_daily_inventory`/`chef_order_readiness` (Chef); `system_meal_windows`/`system_settings`/`system_delivery_stops`/`system_delivery_routes`/`system_hitl_sessions`/`system_payment_webhook_events` (System); `driver_profiles` (Rider).
- **Handoffs → Master:** payment link mint (gateway); inbound payment webhook driving DW1/DW2.
- **New `system_hitl_sessions` interrupt types:** `AWAIT_LOCATION_PIN` (onboarding), `PAYMENT_AWAIT_MASTER_APPROVAL` (customer side of payment). *(Master side: `PAYMENT_AWAIT_PROVIDER`.)*
