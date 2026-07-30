# 👑 Master / Homaatri-System — Table Design & Query Access Map

Milestone 1 table design for the **Homaatri/System domain** (Master Agent), derived from the Master's 12 tools. This is the consolidation pass where all accumulated handoffs, delegated executors, HITL sessions, and webhook plumbing land.

---

## 🔑 Reconciliation (delegate-only supersedes the spec)
The spec says Master has "Global Write Authority" and several tools literally `UPDATE customer_orders`. Per our **delegate-only** rule: **Master writes only `system_*` directly and DELEGATES every subagent-domain write** to that domain's executor (Customer DW1/DW2). All "Master updates customer_*" lines are reclassified as delegations.

## 🧭 Base Rules Applied
1. Master **owns `system_*`** → writes them directly. 2. Cross-domain writes → **delegate** to owner executor (Customer DW1/DW2). 3. Reads global. 4. Master handoff two kinds: target=System → direct; target=subagent → delegate. 5. Phone = natural key. 6. Data types: money `DECIMAL(10,2)`, lat/lng `DECIMAL(10,8)/(11,8)`, `TIMESTAMPTZ`, FK `ON DELETE RESTRICT`.

---

## 📋 Master/System — Query List

Two writes recur (factored out): **W-AUDIT** → `system_agent_logs` (Tools 2–10); **W-OUT** → `system_outbound_queue` (Tools 2,3,5,6,7,8,11).

| Q | Tool | R/W | What it does | Table(s) | Index | On list? |
|---|---|---|---|---|---|---|
| Q1 | 1 validate_cutoff | R | is window open? | `system_meal_windows` (+`system_settings`) | UNIQUE(service_date,meal_type) | ✅ own |
| Q2 | 2 cutoff_batch | R (x-domain) | all CONFIRMED orders+items+coords for window | `customer_orders` ⋈ `customer_order_items` ⋈ `customer_profiles` ⋈ `chef_profiles` | orders(status,meal_window,service_date) | ✅ (Cust/Chef) |
| Q3 | 2 cutoff_batch | W | lock window | `system_meal_windows` | UNIQUE | ✅ own |
| Q4 | 2 cutoff_batch | W | insert route(s) | `system_delivery_routes` | (driver_phone,service_date,meal_type) | ✅ own |
| Q5 | 2 cutoff_batch | W | insert stops (gate-consolidated) | `system_delivery_stops` | UNIQUE(route_id,stop_index) | ✅ own |
| Q6 | 2 cutoff_batch | W | insert stop↔order junction | `system_delivery_stop_orders` | PK(stop_id,order_id); (order_id) | ✅ own |
| Q7 | 2 cutoff_batch | W | log route-opt API run | `system_route_optimization_runs` | (window_id) | ✅ own |
| — | 2 cutoff_batch | DELEGATE | orders CONFIRMED→BATCHED | → Customer DW1 | — | ❌ note · +GCP API |
| Q8 | 3 relay_dietary | W | HITL session (DIETARY_APPROVAL, wait=CHEF) | `system_hitl_sessions` | (status,expires_at) | ✅ own |
| Q9 | 4 cancellation | R (x-domain) | read order + payment | `customer_orders` (+`customer_payments`) | PK | ✅ (Customer) |
| Q10 | 4 cancellation | R | window status (cutoff eligibility) | `system_meal_windows` | UNIQUE | ✅ own |
| — | 4 cancellation | DELEGATE | order→CANCELLED · payment→REFUNDED | → Customer DW1 + DW2 | — | ❌ note · +gateway refund |
| Q11 | 5 order_ready→driver | R | find assigned driver + stop | `system_delivery_stops` ⋈ `system_delivery_routes` (+`driver_profiles`) | stops(target_ref_id,stop_type) | ✅ own(+Rider) |
| — | 5 order_ready→driver | DELEGATE | order→PACKED | → Customer DW1 | — | ❌ note |
| Q12 | 6 gate_delivery | R (x-domain) | customer phones for orders | `customer_orders` (via stop_orders) | (order_id) | ✅ (Customer) |
| Q13 | 6 gate_delivery | W | mark stop COMPLETED | `system_delivery_stops` | (route_id,status) | ✅ own |
| — | 6 gate_delivery | DELEGATE | orders→DELIVERED | → Customer DW1 | — | ❌ note |
| Q14 | 7 unlocatable | R (x-domain) | customer_phone for order | `customer_orders` | PK | ✅ (Customer) |
| Q15 | 7 unlocatable | W | HITL session (UNLOCATABLE_ADDRESS, wait=CUSTOMER) | `system_hitl_sessions` | (status,expires_at) | ✅ own |
| Q16 | 8 traffic_delay | R | remaining stops for route | `system_delivery_stops` | (route_id,status) | ✅ own |
| Q17 | 8 traffic_delay | W | recalc ETAs | `system_delivery_stops` | (route_id) | ✅ own |
| Q18 | 8 traffic_delay | R (x-domain) | affected customers | `customer_orders` (via stop_orders) | (order_id) | ✅ (Customer) |
| Q19 | 9 payment_webhook | W | insert webhook event (idempotency) | `system_payment_webhook_events` | UNIQUE(gateway_event_id) | ✅ own |
| Q20 | 9 payment_webhook | R (x-domain) | find payment + order | `customer_payments` | (gateway_payment_id) | ✅ (Customer) |
| — | 9 payment_webhook | DELEGATE | payment→PAID · order→CONFIRMED | → Customer DW2 + DW1 | — | ❌ note |
| Q21 | 9 payment_webhook | W | resume Customer's + Master's payment interrupts | `system_hitl_sessions` | (order_id) | ✅ own |
| Q22 | 10 delegate_write | W | log delegation | `system_agent_logs` | (order_id,created_at) | ✅ own |
| — | 10 delegate_write | DELEGATE | generic → target executor | → target domain executor | — | ❌ engine |
| Q23 | 11 dispatch_wa | W | enqueue outbound message | `system_outbound_queue` | partial(status=QUEUED) | ✅ own · +Meta API |
| Q24 | 12 log_audit | W | insert audit log | `system_agent_logs` | — | ✅ own |
| Q25 | *(internal)* | W | stop→ARRIVED (from driver mark_reached handoff) | `system_delivery_stops` | (route_id) | ✅ own |

**Write check:** every direct `W` writes `system_*` only ✅. Every subagent-domain write is a delegation to Customer DW1/DW2.

## 🔁 Delegations → Customer executors (closes the loop)
| Executor | Triggered by → status |
|---|---|
| **DW1** `execute_order_status_transition` | cutoff (BATCHED) · cancellation (CANCELLED) · order-ready (PACKED) · gate-delivery (DELIVERED) · payment (CONFIRMED) |
| **DW2** `execute_payment_status_update` | cancellation (REFUNDED) · payment (PAID) |

---

## 🗄️ System-Owned Tables

**Core operational (9):**

| Table | Columns (key) | Keys / Indexes |
|---|---|---|
| `system_meal_windows` | window_id (PK), service_date, meal_type `meal_window_enum`, cutoff_at, status `window_status_enum`, locked_at, total_confirmed_orders | UNIQUE(service_date, meal_type) |
| `system_settings` | key (PK), value `JSONB`, description, updated_at | PK(key) — delivery_fee, cutoff times, radius, tz |
| `system_delivery_routes` | route_id (PK), window_id (FK), service_date, meal_type, driver_phone (FK), total_stops, total_orders, status `route_status_enum`, optimized_at, encoded_polyline (future) | (driver_phone, service_date, meal_type); (window_id) |
| `system_delivery_stops` | stop_id (PK), route_id (FK), stop_index, stop_type `stop_type_enum`, target_ref_id, location_name, latitude `DECIMAL(10,8)`, longitude `DECIMAL(11,8)`, estimated_arrival, actual_arrival, status `stop_status_enum` | UNIQUE(route_id, stop_index); (route_id, status); (target_ref_id, stop_type) |
| `system_delivery_stop_orders` | stop_id (FK), order_id (FK→customer_orders) | PK(stop_id, order_id); (order_id) |
| `system_agent_logs` | log_id (PK), event_type, source_role, target_role, order_id, payload `JSONB`, severity `log_severity_enum`, created_at | (order_id, created_at); (event_type, created_at); partial(severity=CRITICAL) |
| `system_outbound_queue` | message_id (PK), recipient_phone, recipient_role, message_text, message_type, template_name, wa_message_id, status `outbound_status_enum`, attempts, error_detail, related_order_id, created_at, sent_at | partial(status=QUEUED, created_at); (wa_message_id) |
| `system_hitl_sessions` | session_id (PK), thread_id, interrupt_type `hitl_interrupt_type_enum`, waiting_on_role, waiting_on_phone, order_id, payload `JSONB`, default_on_expiry `JSONB`, status `hitl_status_enum`, created_at, expires_at, resolved_at | partial(status=WAITING, expires_at); (order_id); (waiting_on_phone); (thread_id) |
| `system_payment_webhook_events` | event_id (PK), gateway, gateway_event_id, event_type, payment_id, order_id, signature_verified, raw_payload `JSONB`, processing_status, received_at, processed_at | UNIQUE(gateway_event_id); (payment_id) |

**Runtime / infrastructure (shared — NOT a single agent's domain):**

| Table | Purpose | Keys / Indexes |
|---|---|---|
| **`conversation_messages`** | **Unified INSERT-only chat log. Written by the RUNTIME (webhook logs inbound; dispatcher logs outbound — incl. Master's notifications). Global read (Context Assembler fetches last N).** Replaces the 3 `*_chat_history` tables AND `system_inbound_messages`. | (phone, created_at DESC); UNIQUE(wa_message_id) |

`conversation_messages` columns: message_id (PK `VARCHAR(36)`), phone, actor_role (CUSTOMER/CHEF/DRIVER), direction (INBOUND/OUTBOUND), source (USER/CUSTOMER_AGENT/CHEF_AGENT/DRIVER_AGENT/MASTER_AGENT/SYSTEM), message_type (TEXT/LOCATION/INTERACTIVE/IMAGE/TEMPLATE), message_text `TEXT`, latitude/longitude, media_ref, related_order_id, wa_message_id (UNIQUE, inbound dedup), raw_payload `JSONB`, created_at `TIMESTAMPTZ`. **INSERT-only — no update, no delete.** Partition by month at scale.

**Observability (future):**

| Table | Purpose |
|---|---|
| `system_route_optimization_runs` | log each GCP route-opt call (raw req/resp, cost, latency) |

---

## 🔤 New enums (System domain)
`window_status_enum` (OPEN, LOCKED_PROCESSING, COMPLETED) · `route_status_enum` (ASSIGNED, IN_PROGRESS, COMPLETED) · `stop_status_enum` (PENDING, ARRIVED, COMPLETED) · `stop_type_enum` (PICKUP_KITCHEN, DROPOFF_GATE) · `log_severity_enum` (INFO, WARNING, CRITICAL) · `outbound_status_enum` (QUEUED, SENT, DELIVERED, READ, FAILED) · `hitl_status_enum` (WAITING, RESUMED, EXPIRED, RESOLVED) · `hitl_interrupt_type_enum` (DIETARY_APPROVAL, CANCELLATION_APPROVAL, UNLOCATABLE_ADDRESS, AWAIT_LOCATION_PIN, PAYMENT_AWAIT_MASTER_APPROVAL, PAYMENT_AWAIT_PROVIDER) · plus `direction_enum`, `actor_role_enum`, `message_source_enum`, `message_type_enum` for `conversation_messages`.

## 📝 Notes
- **`conversation_messages` is runtime-written**, not an agent tool action — automatic per message, captures Master's outbound too, keeps the write-invariant clean. This is a THIRD table category: **runtime/infrastructure** (alongside domain tables and delegated executors).
- **Context Assembler**: before each LLM call, fetch last 4-5 messages (both directions) for the phone; a guardrail lets the LLM escalate to last-10 (hard cap).
- **Master gap filled (Q25):** driver's stop→ARRIVED handoff = Master direct write to `system_delivery_stops` (system_* is Master's own).
