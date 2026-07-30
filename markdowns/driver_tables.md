# 🚴‍♂️ Rider (Driver) Agent — Table Design & Query Access Map

This document is the **Milestone 1** table design for the **Driver domain**, derived bottom-up from the Driver Agent's 8 tools using the query-inventory method.

---

## 🧭 Base Rules Applied (same for all agents)
1. **Phone is the natural key** (`driver_phone`); transactional rows keep `VARCHAR(36)` ids.
2. **Write invariant** — a tool WRITES only `driver_*`; every cross-domain write is a HANDOFF to Master (note only).
3. **Reads are global** — cross-domain reads stay on the list, finalized under the owner.
4. **Master handoff has two kinds:** if the write targets the **System** domain (Master's own), Master writes it **directly**; if it targets another **subagent** domain, Master **delegates** to that domain's executor.
5. **`driver_locations` dropped** — no GPS; position/progress from `driver_trip_status` + `system_delivery_stops`.
6. **Data types** — lat `DECIMAL(10,8)`/lng `DECIMAL(11,8)`; timestamps `TIMESTAMPTZ`; FKs `ON DELETE RESTRICT`.

---

## 📋 Rider Agent — Query List

| Q | Tool | R/W | What it does | Table(s) | Filter/key cols | Index | On list? |
|---|---|---|---|---|---|---|---|
| Q1 | 1 get_profile | R | identify driver (HOT) | `driver_profiles` | driver_phone | PK(driver_phone) | ✅ |
| Q2 | 2 get_route_itinerary | R (x-domain) | route + ordered stops (+orders per stop) | `system_delivery_routes` ⋈ `system_delivery_stops` ⋈ `system_delivery_stop_orders` | route.driver_phone, meal, date; stops by route_id, stop_index | routes(driver_phone,service_date,meal_type); stops(route_id,stop_index) | ✅ (System) |
| Q3 | 3 dispatch_next_leg | R (x-domain) | next stop (N+1) coords + orders → build Maps link *(reply)* | `system_delivery_stops` (+`system_delivery_stop_orders`) | route_id, stop_index=current+1 | stops(route_id,stop_index) | ✅ (System) |
| Q4 | 4 mark_reached | W | update driver trip phase (AT_KITCHEN / AT_GATE) + current_stop_index | `driver_trip_status` | driver_phone | (driver_phone, service_date) | ✅ own |
| — | 4 mark_reached | HANDOFF | stop → ARRIVED + actual_arrival | → **Master direct** (`system_delivery_stops`, system_* = Master's own) | — | — | ❌ note |
| Q5 | 5 picked_up | W | update driver trip phase (EN_ROUTE_DELIVERY) | `driver_trip_status` | driver_phone | (driver_phone, service_date) | ✅ own |
| Q6 | 5 picked_up | R (x-domain) | next stop for the leg link *(reply)* | `system_delivery_stops` | route_id, stop_index+1 | stops(route_id,stop_index) | ✅ (System) |
| — | 5 picked_up | HANDOFF | orders → PICKED_UP (order_ids) | → **Master → Customer DW1** executor (`customer_orders`) | — | — | ❌ note |
| Q7 | 6 gate_delivered | W | update driver trip phase + current_stop_index | `driver_trip_status` | driver_phone | (driver_phone, service_date) | ✅ own |
| — | 6 gate_delivered | HANDOFF | orders → DELIVERED (→ Customer DW1) · stop → COMPLETED (→ Master direct) · notify customers (→ Master relay) | → **Master** | — | — | ❌ note |
| — | 7 unlocatable_addr | PAUSE/HANDOFF | `interrupt()` `UNLOCATABLE_ADDRESS` (waiting_on=CUSTOMER, checkpoint → `system_hitl_sessions`) → Master → Customer requests pin | → **Master** | — | — | ❌ note |
| — | 8 vehicle_delay | HANDOFF | recalc stop ETAs (→ Master direct `system_delivery_stops`) · alert affected customers (→ Master relay) | → **Master** | — | — | ❌ note |

**Write check:** every direct `W` (Q4, Q5, Q7) writes `driver_*` only ✅. Every operational cross-domain write (stop status, order status, ETAs) is a Master handoff.

---

## 🗺️ Live-Tracking "Progress List" (key role of `driver_trip_status`)

`driver_trip_status` is not just the driver's own bookkeeping — it is the **live-tracking board**, read cross-domain by **Customer & Chef** agents (global read):

> **Progress view = `system_delivery_stops` (ordered drop list) ⋈ `driver_trip_status` (current position + completed).**
> Shows the full route list, which stops are ✅ COMPLETED, where the driver currently is (`current_stop_index`), and what's still PENDING. Replaces live GPS; upgrades to a **map view** later.

Consumers: Customer `get_active_order_status`, Chef `get_assigned_driver_info` / `check_driver_arrival_status`.

---

## 🗄️ Rider-Owned Tables (3 — `driver_locations` dropped)

| Table | Columns | Keys / Indexes |
|---|---|---|
| **`driver_profiles`** | driver_phone (PK), driver_name, vehicle_info, active_status `bool`, current_assigned_route_id (FK→system_delivery_routes), created_at, updated_at | PK(driver_phone) — *Admin-onboarded; driver reads* |
| **`driver_trip_status`** | trip_id (PK `VARCHAR(36)`), driver_phone (FK), route_id (FK→system_delivery_routes), service_date `DATE`, status `trip_status_enum`, current_stop_index `INT`, created_at, updated_at | PK; (driver_phone, service_date) |
| ~~`driver_chat_history`~~ *(superseded)* | Replaced by the unified runtime-written **`conversation_messages`** (see [`master_tables.md`](master_tables.md)) | — |

**New enum introduced:** `trip_status_enum` = (ASSIGNED, EN_ROUTE_PICKUP, AT_KITCHEN, EN_ROUTE_DELIVERY, AT_GATE, COMPLETED).

---

## 🔗 Recorded for Later (not on Rider list)
- **Cross-domain reads → owners:** `system_delivery_routes`, `system_delivery_stops`, `system_delivery_stop_orders`, `system_hitl_sessions` → **System**; writes route to `customer_orders` → **Customer**.
- **Handoffs → Master (two kinds):**
  - **Master direct** (target = System, its own domain): stop → ARRIVED (T4), stop → COMPLETED (T6), ETA recalc (T8).
  - **Master delegates → subagent executor** (target = another domain): `customer_orders` → PICKED_UP (T5) / DELIVERED (T6) → **Customer DW1**.
- **New `system_hitl_sessions` interrupt type:** `UNLOCATABLE_ADDRESS` (waiting_on = CUSTOMER).
- **Master gap surfaced:** Master needs a **stop-status write mechanism** (ARRIVED / COMPLETED / ETA recalc) — not in the current 12 Master tools; add in the Master pass.

## ✅ Resolved Decision
- **Keep `driver_trip_status`** (not derived). It's an own-domain write (free), and doubles as the cross-domain **live-tracking progress board** for Customer & Chef (and a future map view).

## 📝 Observation
The **Rider is the most Master-mediated agent** — its only own-domain writes are trip-phase updates to `driver_trip_status`. All operational state (stop status, order status, ETAs) lives in System/Customer domains, so nearly every driver action is a Master handoff. This is the write-invariant working as designed.
