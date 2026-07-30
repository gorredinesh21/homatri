# 👨‍🍳 Chef Agent — Table Design & Query Access Map

This document is the **Milestone 1** table design for the **Chef domain**, derived bottom-up from the Chef Agent's 9 tools using the query-inventory method.

---

## 🧭 Base Rules Applied (these hold for ALL agents)

1. **Phone is the natural key.** Every domain table keys on the normalized phone (`chef_phone` / `customer_phone` / `driver_phone`). No phone→id resolve step. Actor identity = phone; transactional rows (orders/items/payments/routes) keep surrogate `VARCHAR(36)` ids.
2. **Write invariant.** A subagent tool may **WRITE only its own domain tables**. Any cross-domain write is a **HANDOFF to Master** (subagent → Master → owner domain), recorded as a note only — NOT on this agent's list.
3. **Reads are global.** A tool may **READ any table** directly (zero-latency cross-domain lookups). Cross-domain reads stay on the list; the table is finalized under its owner agent.
4. **Cross-agent calls = notes.** When a tool's code invokes another agent (handoff), that agent's queries are NOT on this list — record the mapping, cover under that agent.
5. **`driver_locations` is dropped.** No live GPS. Rider position is inferred from route progress; arrival = `system_delivery_stops.actual_arrival`.
6. **Data-type standards.** PKs `VARCHAR(36)` (surrogate) or normalized phone; money `DECIMAL(10,2)`; lat `DECIMAL(10,8)` / lng `DECIMAL(11,8)`; timestamps `TIMESTAMPTZ`; FKs `ON DELETE RESTRICT`.

---

## 📋 Chef Agent — Query List

| Q | Tool | R/W | What it does | Table(s) | Filter/key cols | Index implied | On list? |
|---|---|---|---|---|---|---|---|
| Q1 | 1 get_chef_profile | R | identify chef on inbound msg (HOT) | `chef_profiles` | chef_phone | PK(chef_phone) | ✅ |
| Q2 | 2 set_capacity | R | dish name + verify chef owns dish | `chef_menu_items` | menu_item_id, chef_phone | PK; (chef_phone) | ✅ |
| Q3 | 2 set_capacity | W | upsert a dish's daily cap | `chef_daily_inventory` | chef_phone, menu_item_id, service_date | UNIQUE(chef_phone, menu_item_id, service_date) | ✅ own |
| Q4 | 3 toggle_stock | R | verify chef owns dish (pre-cond) | `chef_menu_items` | menu_item_id, chef_phone | PK; (chef_phone) | ✅ |
| Q5 | 3 toggle_stock | W | flip IN/OUT of stock | `chef_menu_items` | menu_item_id | PK | ✅ own |
| Q6 | 4 check_inventory | R | remaining cap + units sold for date | `chef_daily_inventory` ⋈ `chef_menu_items` | chef_phone, service_date | (chef_phone, service_date) | ✅ |
| Q7 | 5 batch_checklist | R (x-domain) | all orders+items for chef this meal/date → totals + itemized + notes | `customer_orders` ⋈ `customer_order_items` | items.chef_phone; orders.meal_window, service_date, status | items(chef_phone); orders(status, meal_window, service_date) | ✅ (Customer) |
| Q8 | 6 mark_packed | R (x-domain) | pre-cond: order is COOKING & chef's | `customer_orders` | order_id | PK | ✅ (Customer) |
| Q9 | 6 mark_packed | W | record readiness signal | `chef_order_readiness` | order_id, chef_phone | (order_id) | ✅ own |
| — | 6 mark_packed | HANDOFF | flip `customer_orders`→PACKED + notify driver | → Master `relay_order_ready_to_driver_tool` | — | — | ❌ note |
| Q10 | 7 get_assigned_driver | R (x-domain) | my pickup stop → its route's driver + ETA | `system_delivery_stops` ⋈ `system_delivery_routes` ⋈ `driver_profiles` | stop.target_ref_id=chef_phone, stop_type=PICKUP_KITCHEN; route.meal,date | stops(target_ref_id, stop_type); routes(driver_phone, service_date, meal_type) | ✅ (System/Rider) |
| Q11 | 8 counter_offer | — | *(reply logged to `conversation_messages` by runtime — not a chef write)* | `conversation_messages` | phone | (phone, created_at DESC) | runtime |
| — | 8 counter_offer | HANDOFF | resume interrupt + relay decision to customer | → Master (hitl resume on `system_hitl_sessions` + counter-offer relay → Customer) | — | — | ❌ note |
| Q12 | 9 check_arrival | R (x-domain) | has driver arrived at my pickup stop? | `system_delivery_stops` ⋈ `system_delivery_stop_orders` ⋈ `system_delivery_routes` ⋈ `driver_profiles` | stop.target_ref_id=chef_phone, stop_type=PICKUP_KITCHEN, date | stops(target_ref_id, stop_type) | ✅ (System/Rider) |

**Write check:** every `W` row (Q3, Q5, Q9) writes a `chef_*` table only ✅. Chat logging moved to the runtime-written `conversation_messages`. Both cross-domain writes (`customer_orders`→PACKED, `system_hitl_sessions` resume) correctly became Master handoffs.

**Query-count note:** an earlier draft counted ~21 (14 substantive + ~7 phone→id resolves). Phone-as-key removed the ~7 resolves; the write invariant moved 2 cross-domain writes to Master handoffs → 12 clean queries. No work lost — resolves were redundant; the 2 writes now live on Master's list.

---

## 🗄️ Chef-Owned Tables (writes land only here)

| Table | Columns | Keys / Indexes |
|---|---|---|
| **`chef_profiles`** | chef_phone (PK, normalized 10-digit), kitchen_name, address, latitude `DECIMAL(10,8)`, longitude `DECIMAL(11,8)`, active_status `bool`, created_at, updated_at `TIMESTAMPTZ` | PK(chef_phone) |
| **`chef_menu_items`** | menu_item_id (PK `VARCHAR(36)`), chef_phone (FK→chef_profiles), dish_name, description, unit_price `DECIMAL(10,2)`, meal_type `meal_window_enum`, is_available `bool` DEFAULT true, created_at, updated_at | PK; idx(chef_phone) |
| **`chef_daily_inventory`** | inventory_id (PK), chef_phone (FK), menu_item_id (FK), service_date `DATE`, max_capacity `INT`(>0), units_sold `INT` DEFAULT 0, created_at, updated_at | UNIQUE(chef_phone, menu_item_id, service_date) |
| **`chef_order_readiness`** | readiness_id (PK), order_id (FK→customer_orders), chef_phone (FK), status `readiness_status_enum`, packed_at `TIMESTAMPTZ`, created_at | idx(order_id) |
| ~~`chef_chat_history`~~ *(superseded)* | Replaced by the unified runtime-written **`conversation_messages`** (see [`master_tables.md`](master_tables.md)) | — |

**New enum introduced:** `readiness_status_enum` = (PREPARING, PACKED_READY). *(Reuses `meal_window_enum`.)*

---

## 🔗 Recorded for Later (not on Chef list)

**Cross-domain reads → finalize under owner:**
- `customer_orders`, `customer_order_items` → **Customer**
- `system_delivery_stops`, `system_delivery_stop_orders`, `system_delivery_routes` → **Homaatri/System**
- `driver_profiles` → **Rider**

**Handoff mappings → cover under Master:**
- Chef Tool 6 → `Master.relay_order_ready_to_driver_tool` *(Master writes `customer_orders`→PACKED via global authority, then notifies Driver)*
- Chef Tool 8 → `Master` *(resume `system_hitl_sessions` + relay counter-offer → Customer)*

---

## ✅ Resolved Decisions
1. **Phone normalization (FINAL)** — canonical = 10-digit Indian, at webhook ingress: strip spaces + leading `+` → **12 digits starting `91`** take last 10 · **10 digits** as-is · else invalid. (Covers `919876543210` that WhatsApp Cloud API sends.)
2. **Order-status write ownership (FINAL)** — Master **DELEGATES ONLY**; it has no direct cross-domain write tool (avoids LLM tool confusion). Cross-domain status flips (PACKED/PICKED_UP/DELIVERED/BATCHED/CANCELLED) are executed by the OWNER domain's deterministic, validated write-executor that Master delegates to. So Chef Tool 6 handoff → Master → `customer_orders` **Customer-domain executor**. Master writes directly only to its own `system_*` domain.
