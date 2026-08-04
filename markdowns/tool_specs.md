# Homaatri — Tool Specifications

Full spec for every tool: inputs, outputs, reads/writes, guards, pauses. Organized **by domain**, then **Same-domain / Cross-domain / Other**. Derived from [user_flows.md](user_flows.md); inventory in [tools_inventory.md](tools_inventory.md).

## Conventions (apply to ALL tools)
- **Tools are agent-driven code.** The agent AI calls a tool; the tool runs plain code. All AI *reasoning* is the agent's job — it picks the tool, and reads the tool's returned message to decide the next step. A tool does **not** spin up its own Gemini call to decide; it checks and messages in code.
- **Guard-then-guide (multiple guards per tool).** Each tool has **one or more `if/else` guards**, and **each guard checks a different precondition and handles it differently**. On a violation the guard (a) does **not** perform the bad write, and (b) returns **its own fixed templated string** telling the agent which tool to use or what to do next — e.g. `create_order` on an existing order → *"use add_item_to_order"*; `create_order` past cutoff → *"order for the next window"*. The agent AI reads that string and re-routes. Code guarantees correctness; the string guarantees recovery.
- **Return shape.** Every tool returns `{ status, ...data, message }` — `status` is machine-readable, `message` is the agent/human-facing string.
- **Classification.**
  - **Same-domain** — touches only its own domain's tables (may *read* other domains freely; no relay, no wait, no cross-domain write).
  - **Cross-domain** — triggers a Master relay, waits on another agent (`interrupt()`), or delegates a write to another domain.
  - **Other** — primitives / scheduled / external / infra.
- **Writes.** *own* = direct via own executor; *cross* = delegate via **Master → owner executor** (+ audit).

---

# CUSTOMER DOMAIN (13 tools)

## Same-domain (10)

### `get_customer_profile`
- **Purpose:** identify caller / check existence.
- **Inputs:** `customer_phone: str`
- **Outputs:** `{status: FOUND|NOT_FOUND, profile?, message}`
- **Guards (if/else):** NOT_FOUND → `"New user — call register_customer"`; FOUND → return profile.
- **Gemini-call inside:** no · **Reads:** `customer_profiles` · **Writes:** none · **Pause:** no · **Executors:** none.

### `register_customer`
- **Purpose:** onboard end-to-end (name+address → location pin → save → auto-show kitchens).
- **Inputs:** `customer_phone: str, name: str, delivery_address: str`
- **Outputs:** `{status: REGISTERED|INVALID_INPUT|ALREADY_EXISTS|TIMEOUT, message}`; on REGISTERED auto-chains `find_nearby_kitchens`.
- **Guards:** missing name/address → `"share your name and full address"`; profile exists → `"already registered — call find_nearby_kitchens"`; no pin / timeout → **rollback** + `"timed out, say hi to restart"`; bad lat/lng → re-prompt for pin.
- **Gemini-call inside:** no · **Reads:** `customer_profiles` · **Writes:** `customer_profiles` (direct) · **Pause:** yes — `send_and_await_reply(LOCATION_PIN)` · **Executors:** `execute_customer_registration_and_location`, `execute_outbound_whatsapp_enqueue`, `execute_conversation_message_insert`.

### `resolve_time_pool`  *(shared read helper)*
- **Purpose:** map current time → orderable window + message.
- **Inputs:** none (optional `now` for tests)
- **Outputs:** `{window: LUNCH|DINNER|TOMORROW_LUNCH, is_open: bool, message}`
- **Guards (if/else on time vs cutoffs):** `< 11:30` → LUNCH; `< 18:30` → DINNER; else → TOMORROW_LUNCH.
- **Gemini-call inside:** no · **Reads:** `system_settings` (cutoffs, tz) · **Writes:** none · **Pause:** no · **Executors:** none.

### `find_nearby_kitchens`
- **Purpose:** nearest kitchens serving the current window.
- **Inputs:** `customer_phone: str, window: str`
- **Outputs:** `{status: OK|NO_LOCATION|NONE_OPEN, kitchens:[{chef_phone, kitchen_name, cuisine, rating, distance_km}], message}`
- **Guards:** no lat/lng on profile → `"need your location — call register_customer"`; none serving window → `"no kitchens serving <window> right now"`.
- **Gemini-call inside:** no · **Reads:** `customer_profiles`(loc), `chef_profiles`, `customer_reviews`(avg rating), `chef_daily_inventory`(window availability) · **Writes:** none · **Pause:** no · **Executors:** none. *(nearest-first, top 5, no hard radius)*

### `view_chef_menu`
- **Purpose:** show a chef's dishes, price, stock for the window.
- **Inputs:** `chef_phone: str, window: str`
- **Outputs:** `{status: OK|NOT_FOUND|NOT_SERVING, dishes:[{item_id, name, price, dietary, spice, in_stock}], message}`
- **Guards:** chef not found → `"kitchen not found"`; no dishes for window → `"this kitchen isn't serving <window>"`.
- **Gemini-call inside:** no · **Reads:** `chef_menu_items`, `chef_daily_inventory` · **Writes:** none · **Pause:** no · **Executors:** none.

### `create_order`
- **Purpose:** atomic create of order header + all items.
- **Inputs:** `customer_phone, chef_phone, service_date, window, items:[{item_id, qty}]`
- **Outputs:** `{status: CREATED|ORDER_EXISTS|CUTOFF_CLOSED, order_id?, subtotal, delivery_fee, total, message}`
- **Guards (if/else):** active order exists → `"ORDER_EXISTS — use add_item_to_order (order_id=<id>)"`; window closed (`resolve_time_pool`) → `"<window> cutoff passed — <next window>"`; else create.
- **Gemini-call inside:** no · **Reads:** `customer_orders`(existing), `system_settings`(fee) · **Writes:** `customer_orders`+`customer_order_items` (direct) · **Pause:** no · **Executors:** `execute_customer_order_initialization`, `execute_add_item_to_order`.

### `add_item_to_order`
- **Purpose:** add / top-up items on an existing modifiable order.
- **Inputs:** `order_id, item_id, qty`
- **Outputs:** `{status: ADDED|NO_ORDER|LOCKED, subtotal, total, message}`
- **Guards:** order not found → `"no active order — call create_order"`; status ∉ {DRAFT_CART, PENDING_PAYMENT} → `"order is locked, can't modify"`.
- **Gemini-call inside:** no · **Reads:** `customer_orders` · **Writes:** `customer_order_items`+`customer_orders` totals (direct) · **Pause:** no · **Executors:** `execute_add_item_to_order`.

### `view_cart`
- **Purpose:** show the current bill.
- **Inputs:** `order_id`
- **Outputs:** `{status, items, subtotal, delivery_fee, total, message}`
- **Guards:** order not found → `"no active order"`.
- **Gemini-call inside:** no · **Reads:** `customer_orders`, `customer_order_items` · **Writes:** none · **Pause:** no · **Executors:** none.

### `submit_order_review`  *(customer feedback)*
- **Purpose:** capture customer feedback — chef & driver ratings + optional written comment.
- **Inputs:** `order_id, chef_rating(1-5), driver_rating(1-5), comment?`
- **Outputs:** `{status: SAVED|NOT_DELIVERED|ALREADY_REVIEWED|BAD_RATING, message}`
- **Guards:** order ≠ DELIVERED → `"you can review after delivery"`; already reviewed → idempotent; rating ∉ 1..5 → template; **if chef_rating ≤ 2 or driver_rating ≤ 2 → also `escalate_to_admin` (cross)**.
- **Gemini-call inside:** no · **Reads:** `customer_orders` · **Writes:** `customer_reviews` (**direct**, `execute_submit_order_review`); low rating → escalate (**delegate**) · **Pause:** no · **Executors:** `execute_submit_order_review` (+ conditional `escalate_to_admin`).

### `get_order_status`
- **Purpose:** live status of the active order (kitchen readiness, driver leg, ETA).
- **Inputs:** `customer_phone` (or `order_id`)
- **Outputs:** `{status: OK|NO_ACTIVE_ORDER, order_status, kitchen_state, driver_state, eta, message}`
- **Guards:** no active order → `"no active order"`.
- **Gemini-call inside:** no · **Reads:** `customer_orders`, `chef_order_readiness`, `system_delivery_stops` · **Writes:** none · **Pause:** no · **Executors:** none.

## Cross-domain (3)

### `request_payment`
- **Purpose:** get a payment link from Master, then wait for confirmation.
- **Inputs:** `order_id`
- **Outputs:** before pause `{status: LINK_SENT, link, message}`; after resume `{status: PAID|FAILED, message}`.
- **Guards:** order status ≠ PENDING_PAYMENT → `"nothing to pay / already confirmed"`; total == 0 → `"cart is empty"`.
- **Gemini-call inside:** no · **Reads:** `customer_orders` · **Writes:** none directly (Master mints + delegates the payment-record write) · **Pause:** yes — `send_and_await_reply(PAYMENT_CONFIRM)`, resumed by the **webhook via Master** · **Relay:** → `Master.mint_payment_link` (deterministic).

### `request_dietary_change`
- **Purpose:** relay a custom/dietary note to the chef; wait for decision (max 2 turns).
- **Inputs:** `order_id, note`
- **Outputs:** `{status: ACCEPTED|REJECTED|COUNTER|KEPT_ORIGINAL, counter?, message}`
- **Guards:** order status ∉ {CONFIRMED, BATCHED, COOKING} → `"too late / not yet confirmed to change"`.
- **Gemini-call inside:** no · **Reads:** `customer_orders` · **Writes:** on ACCEPT the note is saved to the order via **delegate** · **Pause:** yes — `CHEF_DECISION` (≤2 turns; else KEPT_ORIGINAL) · **Relay:** → `Master.relay_dietary_request` (deterministic).

### `cancel_order`
- **Purpose:** cancel before cutoff (auto-cancel + refund); reject after cutoff/cooking.
- **Inputs:** `order_id, reason`
- **Outputs:** `{status: CANCELLED_REFUNDED|CANCELLED|REJECTED_AFTER_CUTOFF, refund_amount?, message}`
- **Guards:** order DELIVERED/CANCELLED → `"cannot cancel"`; window locked & cooking → `REJECTED_AFTER_CUTOFF`; else cancel.
- **Gemini-call inside:** no · **Reads:** `customer_orders`, `system_meal_windows` · **Writes:** order → CANCELLED (**delegate** DW1); if paid → refund (**delegate** DW2 REFUNDED + Razorpay refund via Master) · **Pause:** no · **Relay:** → Master (refund) · **Executors:** DW1, DW2, `payment_service` refund.

---

# CHEF DOMAIN (6 tools)

## Same-domain (4)

### `get_chef_batch`
- **Purpose:** the chef's locked batch — order-wise list + cook-summary.
- **Inputs:** `chef_phone, window, service_date`
- **Outputs:** `{status: OK|NO_BATCH, orders:[{order_id, customer_name, address, items:[{dish, qty, notes}]}], summary:[{dish, total_qty}], message}`
- **Guards:** window not locked / no orders → `"no batch yet for <window>"`.
- **Gemini-call inside:** no · **Reads:** `customer_orders`, `customer_order_items` (this chef, BATCHED) · **Writes:** none · **Pause:** no · **Executors:** none.

### `get_chef_profile`
- **Purpose:** identify a chef on inbound.
- **Inputs:** `chef_phone`
- **Outputs:** `{status: FOUND|NOT_FOUND, profile?, message}`
- **Gemini-call inside:** no · **Reads:** `chef_profiles` · **Writes:** none · **Executors:** none.

### `set_daily_capacity`
- **Purpose:** chef sets max portions per dish for a date/window.
- **Inputs:** `chef_phone, menu_item_id, service_date, window, max_capacity, is_unlimited?`
- **Outputs:** `{status: SET, message}`
- **Guards:** not this chef's dish → template; `max_capacity < 0` → template.
- **Gemini-call inside:** no · **Reads:** `chef_menu_items` · **Writes:** `chef_daily_inventory` (**direct**) · **Executors:** `execute_daily_capacity_upsert`.

### `toggle_dish_stock`
- **Purpose:** mark a dish in/out of stock mid-day.
- **Inputs:** `chef_phone, menu_item_id, is_available`
- **Outputs:** `{status: UPDATED, message}`
- **Gemini-call inside:** no · **Reads:** `chef_menu_items` · **Writes:** `chef_daily_inventory` (**direct**) · **Executors:** `execute_dish_stock_toggle`.

## Cross-domain (2)

### `respond_to_dietary_request`
- **Purpose:** chef's decision on a customer dietary request; resumes the waiting customer.
- **Inputs:** `hitl_session_id, decision: ACCEPTED|REJECTED|COUNTER, counter_note?`
- **Outputs:** `{status: RESOLVED|COUNTER_SENT, message}`
- **Guards:** hitl missing/expired → `"request expired"`; COUNTER without `counter_note` → `"add your counter details"`.
- **Gemini-call inside:** no · **Reads:** `system_hitl_sessions` · **Writes:** HITL update (**delegate** → `execute_hitl_session_create_or_resume`); on ACCEPT the note → `customer_order_items` (**delegate**) · **Pause:** no — **resumes the customer's paused thread** via Master · **Relay:** → Master.

### `mark_order_ready`
- **Purpose:** mark food packed; notify the assigned driver.
- **Inputs:** `order_id, box_count?, notes?`
- **Outputs:** `{status: READY|ALREADY_READY|NOT_COOKING, message}`
- **Guards:** order ∉ {BATCHED, COOKING} → `"not ready to pack yet"`; already ready → idempotent.
- **Gemini-call inside:** no · **Reads:** `customer_orders`, `system_delivery_stops` (driver) · **Writes:** `chef_order_readiness` (**direct**, `execute_order_readiness_record`); order → PACKED (**delegate** DW1) · **Pause:** no · **Relay:** → `Master.relay_order_ready_to_driver`.

---

# DRIVER DOMAIN (8 tools)

## Same-domain (4)

### `get_driver_profile`
- **Purpose:** identify a driver on inbound.
- **Inputs:** `driver_phone`
- **Outputs:** `{status: FOUND|NOT_FOUND, profile?, message}`
- **Gemini-call inside:** no · **Reads:** `driver_profiles` · **Writes:** none · **Executors:** none.

### `register_driver`
- **Purpose:** onboard a driver (name, vehicle).
- **Inputs:** `driver_phone, driver_name, vehicle_type, vehicle_number`
- **Outputs:** `{status: REGISTERED|EXISTS|INVALID, message}`
- **Guards:** missing fields → template; exists → template.
- **Gemini-call inside:** no · **Reads:** `driver_profiles` · **Writes:** `driver_profiles` (**direct**) · **Executors:** `execute_driver_profile_upsert`.

### `update_duty_status`
- **Purpose:** driver goes on/off duty (availability).
- **Inputs:** `driver_phone, on_duty: bool`
- **Outputs:** `{status: ON_DUTY|OFF_DUTY, message}`
- **Gemini-call inside:** no · **Writes:** `driver_profiles` (**direct**) · **Executors:** `execute_driver_profile_upsert`.

### `get_driver_route`
- **Purpose:** the driver's route — surface **only the next leg**.
- **Inputs:** `driver_phone`
- **Outputs:** `{status: OK|NO_ROUTE, route_id, next_stop:{index, type, location, address, maps_url, orders:[...]}, progress, message}`
- **Guards:** no assigned route → `"no route assigned yet"`.
- **Gemini-call inside:** no · **Reads:** `system_delivery_routes`, `system_delivery_stops`, `system_delivery_stop_orders` · **Writes:** none · **Pause:** no · **Executors:** none.

## Cross-domain (4)

### `confirm_pickup`
- **Purpose:** driver picked up at the kitchen; advance + reveal next leg.
- **Inputs:** `driver_phone, stop_id`
- **Outputs:** `{status: PICKED_UP|WRONG_STOP|ALREADY_DONE, next_stop?, message}`
- **Guards:** stop not this driver's / not PICKUP → template; already completed → idempotent.
- **Gemini-call inside:** no · **Reads:** stops, stop_orders, routes · **Writes:** `driver_trip_status` (**direct**, `execute_driver_trip_phase_update`); orders → PICKED_UP (**delegate** DW1); stop → COMPLETED (**delegate** `execute_stop_status_update`) · **Pause:** no.

### `confirm_delivery`
- **Purpose:** gate delivery — bulk DELIVERED with individual exceptions; reveal next / finish.
- **Inputs:** `driver_phone, stop_id, undelivered_ids?`
- **Outputs:** `{status: DELIVERED|PARTIAL|WRONG_STOP, delivered_ids, undelivered_ids, next_stop?, message}`
- **Guards:** stop not driver's / not DROPOFF → template; already completed → idempotent.
- **Gemini-call inside:** no · **Reads:** stops, stop_orders · **Writes:** orders → DELIVERED **bulk except `undelivered_ids`** (**delegate** DW1); stop → COMPLETED (**delegate**); `driver_trip_status` (**direct**) · **Pause:** no · **Relay:** → `Master.relay_delivery_completed_to_customer`.

### `ask_chef_status`
- **Purpose:** driver asks the chef "ready?" before/at pickup.
- **Inputs:** `driver_phone, chef_phone`
- **Outputs:** `{status: SENT, message}`
- **Guards:** chef not on driver's route → `"not your assigned kitchen"`.
- **Gemini-call inside:** no · **Reads:** routes/stops · **Writes:** none direct · **Pause:** optional (await `CHEF_REPLY`) · **Relay:** → `Master.relay_driver_query_to_chef`.

### `report_address_issue`
- **Purpose:** address not found → request a fresh location pin from the customer.
- **Inputs:** `driver_phone, stop_id, order_id`
- **Outputs:** `{status: PIN_REQUESTED|RESOLVED, new_location?, message}`
- **Guards:** stop not driver's / order not at stop → template.
- **Gemini-call inside:** no · **Reads:** stops, orders · **Writes:** on new pin → customer location (**delegate**) · **Pause:** waits for the customer's pin (customer-side `send_and_await_reply`) · **Relay:** → `Master.relay_address_issue_to_customer`.

---

# MASTER DOMAIN (13 tools)

## Same-domain (3)

### `call_maps_route`  *(external helper)*
- **Purpose:** Google Maps route optimization; ordered stops + legs.
- **Inputs:** `stops:[{lat, lng}]`
- **Outputs:** `{ordered_stops, total_distance_km, duration_mins, maps_url}`
- **Guards:** `<2` stops → single leg; API error → haversine fallback order.
- **Gemini-call inside:** no · **Reads:** none · **Writes:** none (engine persists) · **External:** Google Maps.

### `get_kitchen_availability_summary`  *(oversight read)*
- **Purpose:** capacity/stock across kitchens for a window.
- **Inputs:** `window, service_date`
- **Outputs:** `{kitchens:[{chef, dishes_available, capacity_left}], message}`
- **Gemini-call inside:** no · **Reads:** `chef_profiles`, `chef_menu_items`, `chef_daily_inventory` · **Writes:** none · **Executors:** none.

### `get_order_pipeline_summary`  *(oversight read)*
- **Purpose:** order counts by status for a window.
- **Inputs:** `window, service_date`
- **Outputs:** `{counts:{PENDING_PAYMENT, CONFIRMED, BATCHED, COOKING, PACKED, PICKED_UP, DELIVERED, CANCELLED}, message}`
- **Gemini-call inside:** no · **Reads:** `customer_orders` · **Writes:** none · **Executors:** none.

## Cross-domain (10)

### `mint_payment_link`
- **Purpose:** create a Razorpay link; store the payment record.
- **Inputs:** `order_id, amount, customer_phone`
- **Outputs:** `{status: LINK_CREATED|BAD_ORDER, link, plink_id, message}`
- **Guards:** order ≠ PENDING_PAYMENT → template; amount ≤ 0 → template.
- **Gemini-call inside:** no · **Reads:** `customer_orders` · **Writes:** `customer_payments` (**delegate** `execute_payment_record_creation`) + audit · **External:** Razorpay (`payment_service`) · **Executors:** `execute_payment_record_creation`, `execute_system_audit_log`.

### `process_payment_webhook`
- **Purpose:** verify + confirm payment; resume the customer.
- **Inputs:** `payload{gateway_event_id, order_id, payment_id, amount, signature}`
- **Outputs:** `{status: SUCCESS|IDEMPOTENT_SKIPPED|INVALID_SIGNATURE, order_status, message}`
- **Guards:** bad HMAC → INVALID_SIGNATURE; duplicate event → IDEMPOTENT_SKIPPED.
- **Gemini-call inside:** no · **Reads:** `customer_orders`, `customer_payments` · **Writes:** `system_payment_webhook_events` (**direct**); payment → PAID + order → CONFIRMED (**delegate** DW2→DW1) · **Resumes** the customer thread via `Command(resume, thread_id=phone)` · **External:** `payment_service` HMAC · **Executors:** `execute_payment_webhook_idempotency_log`, DW2 `execute_payment_status_update`(→DW1), `execute_system_audit_log`.

### `run_cutoff_batch`  *(scheduled engine — Cloud Scheduler)*
- **Purpose:** at cutoff — lock window, allocate driver, optimize route, create route/stops, dispatch chef & driver.
- **Inputs:** `window, service_date` (from scheduler)
- **Outputs:** `{status: BATCHED|NO_ORDERS, route_id?, total_orders, total_stops, message}`
- **Guards:** no CONFIRMED orders → NO_ORDERS (skip); window already locked → idempotent.
- **Gemini-call inside:** no · **Reads:** `customer_orders`(CONFIRMED), `chef_profiles`, `customer_profiles`(loc) · **Writes:** `system_meal_windows` lock + routes/stops/stop_orders (**direct**); order → BATCHED (**delegate** DW1); driver trip (**delegate**) · **Uses:** `allocate_driver`, `call_maps_route`, `execute_outbound_whatsapp_enqueue` · **Executors:** `execute_meal_window_lock_and_creation`, `execute_cutoff_batch_lock_and_routes_creation`(→DW1), `execute_outbound_whatsapp_enqueue`, `execute_system_audit_log`.

### `allocate_driver`
- **Purpose:** assign a driver to the batch (1:1, by location).
- **Inputs:** `window, service_date, chef_phone`
- **Outputs:** `{status: ASSIGNED|NO_DRIVER, driver_phone?, message}`
- **Guards:** no available driver → NO_DRIVER → `escalate_to_admin`.
- **Gemini-call inside:** no · **Reads:** `driver_profiles`, `driver_trip_status` · **Writes:** `driver_trip_status` (**delegate** `execute_driver_trip_initialization`) · **Executors:** `execute_driver_trip_initialization`.

### `relay_dietary_request`
- **Purpose:** route customer's dietary note to the chef; hold HITL; enforce 2-turn cap; resume customer.
- **Inputs:** `order_id, customer_phone, note, turn?`
- **Outputs:** `{status: SENT_TO_CHEF|RESOLVED|KEPT_ORIGINAL, decision?, message}`
- **Guards:** `turn > 2` → KEPT_ORIGINAL; chef not found → template.
- **Gemini-call inside:** no · **Reads:** `customer_orders` · **Writes:** `system_hitl_sessions` (**direct**); on ACCEPT note → `customer_order_items` (**delegate**); outbound · **Executors:** `execute_hitl_session_create_or_resume`, `execute_outbound_whatsapp_enqueue`.

### `relay_order_ready_to_driver`
- **Purpose:** notify the assigned driver food is packed.
- **Inputs:** `order_id`
- **Outputs:** `{status: DRIVER_NOTIFIED|NO_DRIVER, driver_phone?, message}`
- **Gemini-call inside:** no · **Reads:** `system_delivery_stops/routes` · **Writes:** outbound (**direct**) · **Executors:** `execute_outbound_whatsapp_enqueue`.

### `relay_driver_query_to_chef`
- **Purpose:** route a driver's "ready?" query to the chef.
- **Inputs:** `driver_phone, chef_phone, query`
- **Outputs:** `{status: SENT, message}`
- **Gemini-call inside:** no · **Writes:** outbound · **Executors:** `execute_outbound_whatsapp_enqueue`.

### `relay_address_issue_to_customer`
- **Purpose:** ask the customer for a fresh location pin; on pin, update + notify driver.
- **Inputs:** `order_id, customer_phone, driver_phone`
- **Outputs:** `{status: PIN_REQUESTED|UPDATED, new_location?, message}`
- **Gemini-call inside:** no · **Writes:** on pin → customer location (**delegate**); outbound · **Pause:** customer-side `send_and_await_reply(LOCATION_PIN)` · **Executors:** outbound + delegated customer update.

### `relay_delivery_completed_to_customer`
- **Purpose:** notify customers their gate delivery is done.
- **Inputs:** `order_ids, gate_name`
- **Outputs:** `{status: CUSTOMERS_NOTIFIED, count, message}`
- **Gemini-call inside:** no · **Writes:** outbound · **Executors:** `execute_outbound_whatsapp_enqueue`.

### `escalate_to_admin`  *(the ONE Master reasoning turn)*
- **Purpose:** escalate an exception (no driver, repeated failure, ambiguous case) to the human **Admin**.
- **Inputs:** `context{type, refs, summary}`
- **Outputs:** `{status: ESCALATED, hitl_id, message}`
- **AI note:** the *decision to escalate* is a **Master agent-level** reasoning turn — the tool itself is still pure code (records the escalation).
- **Reads:** relevant refs · **Writes:** `system_hitl_sessions` (**direct**, `waiting_on=ADMIN`) + audit · **Executors:** `execute_hitl_session_create_or_resume`, `execute_system_audit_log`.

---

# SHARED INFRASTRUCTURE (2 primitives — not a domain)

### `send_and_await_reply`
- **Purpose:** send an outbound WhatsApp message, then **pause** until a specific reply type arrives.
- **Inputs:** `recipient_phone, message, await_type: LOCATION_PIN|PAYMENT_CONFIRM|CHEF_DECISION|CHEF_REPLY, timeout_mins`
- **Outputs (on resume):** the awaited payload, or `TIMEOUT`.
- **Mechanism:** enqueue outbound → `interrupt(await_type)` → resume via `Command(resume, thread_id)`. On timeout / superseding message → **pending-state rollback**.
- **Gemini-call inside:** no · **Executors:** `execute_outbound_whatsapp_enqueue`, `execute_hitl_session_create_or_resume`, `execute_conversation_message_insert`.

### `delegate_write`
- **Purpose:** the single choke-point for **cross-domain writes** — routes an agent's cross-domain write to the owner executor + audits it.
- **Inputs:** `requesting_role, target(owner executor/table), payload`
- **Outputs:** `{status: WRITTEN|DENIED, result, message}`
- **Guards:** `requesting_role` not permitted for `target` → DENIED.
- **Gemini-call inside:** no · **Writes:** via the owner executor + audit · **Executors:** (owner executor) + `execute_system_audit_log`.

---

# NOTES & OPEN FOUNDATION GAP
- **Customer feedback** = `submit_order_review` (ratings + written comment), with **auto-escalation to Admin** when a rating ≤ 2.
- **Chef onboarding & menu catalog are Admin/seed-managed.** There is **no `chef_profiles` or `chef_menu_items` write executor** in the frozen 21 — so there is deliberately **no `register_chef` or dish-CRUD agent tool**; chefs are onboarded and their menus created by Admin/seed. If self-service chef onboarding is wanted later, it needs **one new executor** (a controlled unfreeze). *Decision parked.*
- **Grand total specced: 42 tools** — Customer 13, Chef 6, Driver 8, Master 13, Shared infra 2.
