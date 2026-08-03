# Homaatri — Derived Tool Inventory

Tools **derived from the flows** in [user_flows.md](user_flows.md) via the sequence-diagram method (§12). Grows one flow at a time. This is the contract for the rebuild's tool layer.

Legend — Type: **READ** (any table, no restriction) · **WRITE** (own domain only) · **RELAY** (cross-domain, Master-mediated) · **PRIMITIVE** (shared runtime atom).
Status: 🆕 to build · ✅ bedrock (executor exists) · ⏳ later flow.

---

## Shared runtime primitives
| Primitive | Signature | Purpose |
|---|---|---|
| `send_and_await_reply` 🆕 | `(recipient_phone, message, await_type)` | Enqueue an outbound WhatsApp message, then **pause on a checkpoint** until a reply of `await_type` (e.g. `LOCATION_PIN`, `CHEF_DECISION`, `PAYMENT_CONFIRM`) arrives. The single reusable ask-and-wait atom for every ⏸️ pause. Subject to the ⭐ pending-state-rollback invariant (timeout / new-message → rollback). |

---

## Tool conventions
- **Config from `system_settings`** — cutoff times, timezone, delivery fee are **read from the table**, never hardcoded (runtime flexibility).
- **Guard-then-guide** — a precondition guard does two jobs: (a) **code prevents** the invalid write (deterministic safety — e.g. no double order-create), and (b) the tool **returns a natural-language + structured guidance string** telling the LLM the correct next tool (UX recovery). Code guarantees correctness; the string guarantees the right recovery. Never rely on the string alone for an invariant. e.g. `create_order` on an existing active order → returns `{status:"ERROR_ORDER_EXISTS", order_id, use_tool:"add_item_to_order"}` + prose.

---

## Customer Agent tools

### From Flow 1 — Onboarding
| Tool | Type | Reuses (bedrock) | Notes |
|---|---|---|---|
| `get_customer_profile(phone)` 🆕 | READ | — | existence + identity check; returns profile or NOT_FOUND |
| `register_customer(phone, name, address)` 🆕 | WRITE | `execute_customer_registration_and_location` ✅ | validates basics → calls `send_and_await_reply(..., LOCATION_PIN)` → validates lat/lng → saves → **auto-chains** to `find_nearby_kitchens` (no pause) |
| `find_nearby_kitchens(phone, window)` 🆕 | READ | — | Haversine over `chef_profiles`; **filtered to current time-pool window**; rating = **avg of that chef's `customer_reviews`**; sorted nearest-first; **top 5**, no hard radius; returns name + cuisine + rating + distance |

**Bedrock executors reused (no new code):** `execute_conversation_message_insert`, `execute_customer_registration_and_location`, `execute_outbound_whatsapp_enqueue`.

### From Flows 2–3 — Time-pool, Browse & Order
| Tool | Type | Reuses (bedrock) | Notes |
|---|---|---|---|
| `resolve_time_pool()` 🆕 | READ | — | reads cutoffs from **`system_settings`**; returns `{window, is_open, message}` per §5 table |
| `view_chef_menu(chef_phone, window)` 🆕 | READ | — | reads `chef_menu_items` + `chef_daily_inventory` (stock, availability) |
| `create_order(phone, chef_phone, date, window, items[])` 🆕 | WRITE | `execute_customer_order_initialization` ✅ + `execute_add_item_to_order` ✅ | **atomic** header+items; 🛡️ cutoff re-check via `resolve_time_pool`; **guard-then-guide**: if an active order exists → return guidance to use `add_item_to_order` (no double-create) |
| `add_item_to_order(order_id, item_id, qty)` 🆕 | WRITE | `execute_add_item_to_order` ✅ | later top-ups to an existing order |
| `view_cart(order_id)` 🆕 | READ | — | reads `customer_orders` + `customer_order_items` → subtotal + delivery_fee + total |

### From Flow 4 — Payment (Customer side)
| Tool | Type | Reuses | Notes |
|---|---|---|---|
| `request_payment(order_id)` 🆕 | RELAY | — | invokes Master `mint_payment_link`; sends link to user; then **`interrupt()`** awaiting `PAYMENT_CONFIRM` (resumed by the webhook via Master — see user_flows §11) |

### From Flow 6 — Dietary request (Customer side)
| Tool | Type | Reuses | Notes |
|---|---|---|---|
| `request_dietary_change(order_id, note)` 🆕 | RELAY | — | hands note to Master; ⏸️ awaits `CHEF_DECISION`; handles counter-offer (max 2 turns; else keep original) |

---

## Chef Agent tools

### From Flow 6 — Batch view, dietary response, ready-relay
| Tool | Type | Reuses (bedrock) | Notes |
|---|---|---|---|
| `get_chef_batch(chef, window, date)` 🆕 | READ | — | **order-wise** (items + address per order) + consolidated cook-summary at bottom |
| `respond_to_dietary_request(hitl_id, decision, counter?)` 🆕 | WRITE(sys HITL) | `execute_hitl_session_create_or_resume` ✅ | accept / reject / counter |
| `mark_order_ready(order_id, box_count?, notes?)` 🆕 | WRITE(own) | `execute_order_readiness_record` ✅ | then triggers Master `relay_order_ready_to_driver` |

## Driver Agent tools
_(to be derived — Flow 7)_

## Master Agent tools

### From Flow 4 — Payment (Master owns the gateway, both legs)
| Tool | Type | Reuses (bedrock/infra) | Notes |
|---|---|---|---|
| `mint_payment_link(order_id, amount, phone)` 🆕 | WRITE(sys)+delegate | `payment_service` ✅; delegates `execute_payment_record_creation` ✅ + `execute_system_audit_log` ✅ | calls Razorpay, stores `plink_id` on `customer_payments` via delegation, returns link |
| `process_payment_webhook(payload)` 🆕 | WRITE(sys)+delegate | `payment_service` HMAC ✅; `execute_payment_webhook_idempotency_log` ✅; delegates DW2→DW1 ✅ | verify + idempotent; on PAID → payment PAID + order CONFIRMED → **resumes the customer's paused thread** via `Command(resume, thread_id=phone)` |

### From Flow 5 — Cutoff & Batch (scheduled background engine — no user-facing latency)
**Trigger:** GCP **Cloud Scheduler** at 11:30 / 18:30 → internal endpoint. Runs as a background job; even if it takes tens of seconds (Maps API), nobody waits.
| Tool | Type | Reuses (bedrock/infra) |
|---|---|---|
| `run_cutoff_batch(window, date)` 🆕 | engine | orchestrates the steps below |
| `allocate_driver(window, date)` 🆕 | WRITE via delegate | `execute_driver_trip_initialization` ✅ (driver table) |
| `call_maps_route(stops)` 🆕 | external | Google Maps infra |

**Reuses existing:** `execute_meal_window_lock_and_creation` ✅, `execute_cutoff_batch_lock_and_routes_creation` ✅ (→ DW1 order→BATCHED), `execute_outbound_whatsapp_enqueue` ✅, `execute_system_audit_log` ✅.

**Write ownership (Flow 5):** `system_*` (window, routes, stops, stop_orders, outbound, audit) = **direct**; order → BATCHED = **delegate DW1**; driver trip = **delegate `execute_driver_trip_initialization`**.

### From Flow 6 — Relays (deterministic routers)
| Tool | Type | Reuses | Notes |
|---|---|---|---|
| `relay_dietary_request(order_id, note)` 🆕 | RELAY (deterministic) | `execute_hitl_session_create_or_resume` ✅; on accept delegates note-write to customer executor | routes customer↔chef, holds HITL, enforces 2-turn cap |
| `relay_order_ready_to_driver(order_id)` 🆕 | RELAY (deterministic) | reads route → `execute_outbound_whatsapp_enqueue` ✅ | notifies assigned driver food is packed |

_(more Master tools — Flow 7 relays + Flow 8)_

---

## Cross-domain dependency notes (for the SCC/build-order graph)
- `find_nearby_kitchens` (Customer) **reads** Chef-domain tables (`chef_profiles`, `customer_reviews`, availability) → cross-domain READ (allowed; not a cycle).
- No write cycles in Flows 1–3 (onboarding + ordering are linear chains).
- **SCC #1 = {Customer Agent, Master Agent}** (Flow 4): Customer calls Master to mint; Master resumes Customer on the webhook → mutual dependency → **designed together**.
- **Resume triggers:** a paused thread (`interrupt()`) resumes on EITHER a user inbound message OR a system event (payment webhook), via `Command(resume=..., thread_id=phone)`.
- **Flow 5 (cutoff):** background/scheduled (Cloud Scheduler); Master writes `system_*` directly and delegates order→BATCHED (DW1) + driver-trip (driver executor). No cycle; no user latency.
- **Latency model:** interactive turn ≈ 2–6 s (1–2 LLM calls); each extra *synchronous* agent hop adds ~1–3 s → keep mechanical relays deterministic. Slow waits use save/resume (no spinner). Scheduled flows = 0 user latency.
- **Master relays are deterministic** (routing + HITL/turn-management, no LLM turn). Master takes an LLM turn only for Master-level decisions (escalate to Admin, exceptions). Domain judgment stays at the spokes.
- **SCC collapse:** because Master mediates every cross-domain path, {Customer, Chef, Driver, Master} form **one cluster through Master** → design Master + its relays as one coherent unit, then the spokes.
