# Homaatri — Tool Specifications

Full spec for every tool: inputs, outputs, reads/writes, guards, pauses. Organized **by domain**, then **Same-domain / Cross-domain / Other**. Derived from [user_flows.md](user_flows.md); inventory in [tools_inventory.md](tools_inventory.md).

## Conventions (apply to ALL tools)
- **No AI inside tools.** Every tool is pure deterministic code. AI reasoning happens only at the **agent level** (before = pick the tool, after = word the reply).
- **Guard-then-guide.** Each tool validates preconditions with `if/else`. On a violation it (a) **does not** perform the bad write, and (b) returns a **fixed templated string** telling the agent what to do next. The agent AI reads that string and re-routes. Code guarantees correctness; the string guarantees recovery.
- **Return shape.** Every tool returns `{ status, ...data, message }` — `status` is machine-readable, `message` is the agent/human-facing string.
- **Classification.**
  - **Same-domain** — touches only its own domain's tables (may *read* other domains freely; no relay, no wait, no cross-domain write).
  - **Cross-domain** — triggers a Master relay, waits on another agent (`interrupt()`), or delegates a write to another domain.
  - **Other** — primitives / scheduled / external / infra.
- **Writes.** *own* = direct via own executor; *cross* = delegate via **Master → owner executor** (+ audit).

---

# CUSTOMER DOMAIN (10 tools)

## Same-domain (8)

### `get_customer_profile`
- **Purpose:** identify caller / check existence.
- **Inputs:** `customer_phone: str`
- **Outputs:** `{status: FOUND|NOT_FOUND, profile?, message}`
- **Guards (if/else):** NOT_FOUND → `"New user — call register_customer"`; FOUND → return profile.
- **AI inside:** no · **Reads:** `customer_profiles` · **Writes:** none · **Pause:** no · **Executors:** none.

### `register_customer`
- **Purpose:** onboard end-to-end (name+address → location pin → save → auto-show kitchens).
- **Inputs:** `customer_phone: str, name: str, delivery_address: str`
- **Outputs:** `{status: REGISTERED|INVALID_INPUT|ALREADY_EXISTS|TIMEOUT, message}`; on REGISTERED auto-chains `find_nearby_kitchens`.
- **Guards:** missing name/address → `"share your name and full address"`; profile exists → `"already registered — call find_nearby_kitchens"`; no pin / timeout → **rollback** + `"timed out, say hi to restart"`; bad lat/lng → re-prompt for pin.
- **AI inside:** no · **Reads:** `customer_profiles` · **Writes:** `customer_profiles` (direct) · **Pause:** yes — `send_and_await_reply(LOCATION_PIN)` · **Executors:** `execute_customer_registration_and_location`, `execute_outbound_whatsapp_enqueue`, `execute_conversation_message_insert`.

### `resolve_time_pool`  *(shared read helper)*
- **Purpose:** map current time → orderable window + message.
- **Inputs:** none (optional `now` for tests)
- **Outputs:** `{window: LUNCH|DINNER|TOMORROW_LUNCH, is_open: bool, message}`
- **Guards (if/else on time vs cutoffs):** `< 11:30` → LUNCH; `< 18:30` → DINNER; else → TOMORROW_LUNCH.
- **AI inside:** no · **Reads:** `system_settings` (cutoffs, tz) · **Writes:** none · **Pause:** no · **Executors:** none.

### `find_nearby_kitchens`
- **Purpose:** nearest kitchens serving the current window.
- **Inputs:** `customer_phone: str, window: str`
- **Outputs:** `{status: OK|NO_LOCATION|NONE_OPEN, kitchens:[{chef_phone, kitchen_name, cuisine, rating, distance_km}], message}`
- **Guards:** no lat/lng on profile → `"need your location — call register_customer"`; none serving window → `"no kitchens serving <window> right now"`.
- **AI inside:** no · **Reads:** `customer_profiles`(loc), `chef_profiles`, `customer_reviews`(avg rating), `chef_daily_inventory`(window availability) · **Writes:** none · **Pause:** no · **Executors:** none. *(nearest-first, top 5, no hard radius)*

### `view_chef_menu`
- **Purpose:** show a chef's dishes, price, stock for the window.
- **Inputs:** `chef_phone: str, window: str`
- **Outputs:** `{status: OK|NOT_FOUND|NOT_SERVING, dishes:[{item_id, name, price, dietary, spice, in_stock}], message}`
- **Guards:** chef not found → `"kitchen not found"`; no dishes for window → `"this kitchen isn't serving <window>"`.
- **AI inside:** no · **Reads:** `chef_menu_items`, `chef_daily_inventory` · **Writes:** none · **Pause:** no · **Executors:** none.

### `create_order`
- **Purpose:** atomic create of order header + all items.
- **Inputs:** `customer_phone, chef_phone, service_date, window, items:[{item_id, qty}]`
- **Outputs:** `{status: CREATED|ORDER_EXISTS|CUTOFF_CLOSED, order_id?, subtotal, delivery_fee, total, message}`
- **Guards (if/else):** active order exists → `"ORDER_EXISTS — use add_item_to_order (order_id=<id>)"`; window closed (`resolve_time_pool`) → `"<window> cutoff passed — <next window>"`; else create.
- **AI inside:** no · **Reads:** `customer_orders`(existing), `system_settings`(fee) · **Writes:** `customer_orders`+`customer_order_items` (direct) · **Pause:** no · **Executors:** `execute_customer_order_initialization`, `execute_add_item_to_order`.

### `add_item_to_order`
- **Purpose:** add / top-up items on an existing modifiable order.
- **Inputs:** `order_id, item_id, qty`
- **Outputs:** `{status: ADDED|NO_ORDER|LOCKED, subtotal, total, message}`
- **Guards:** order not found → `"no active order — call create_order"`; status ∉ {DRAFT_CART, PENDING_PAYMENT} → `"order is locked, can't modify"`.
- **AI inside:** no · **Reads:** `customer_orders` · **Writes:** `customer_order_items`+`customer_orders` totals (direct) · **Pause:** no · **Executors:** `execute_add_item_to_order`.

### `view_cart`
- **Purpose:** show the current bill.
- **Inputs:** `order_id`
- **Outputs:** `{status, items, subtotal, delivery_fee, total, message}`
- **Guards:** order not found → `"no active order"`.
- **AI inside:** no · **Reads:** `customer_orders`, `customer_order_items` · **Writes:** none · **Pause:** no · **Executors:** none.

## Cross-domain (2)

### `request_payment`
- **Purpose:** get a payment link from Master, then wait for confirmation.
- **Inputs:** `order_id`
- **Outputs:** before pause `{status: LINK_SENT, link, message}`; after resume `{status: PAID|FAILED, message}`.
- **Guards:** order status ≠ PENDING_PAYMENT → `"nothing to pay / already confirmed"`; total == 0 → `"cart is empty"`.
- **AI inside:** no · **Reads:** `customer_orders` · **Writes:** none directly (Master mints + delegates the payment-record write) · **Pause:** yes — `send_and_await_reply(PAYMENT_CONFIRM)`, resumed by the **webhook via Master** · **Relay:** → `Master.mint_payment_link` (deterministic).

### `request_dietary_change`
- **Purpose:** relay a custom/dietary note to the chef; wait for decision (max 2 turns).
- **Inputs:** `order_id, note`
- **Outputs:** `{status: ACCEPTED|REJECTED|COUNTER|KEPT_ORIGINAL, counter?, message}`
- **Guards:** order status ∉ {CONFIRMED, BATCHED, COOKING} → `"too late / not yet confirmed to change"`.
- **AI inside:** no · **Reads:** `customer_orders` · **Writes:** on ACCEPT the note is saved to the order via **delegate** · **Pause:** yes — `CHEF_DECISION` (≤2 turns; else KEPT_ORIGINAL) · **Relay:** → `Master.relay_dietary_request` (deterministic).

---

# CHEF DOMAIN (3 tools)
_(to spec next)_

# DRIVER DOMAIN (5 tools)
_(to spec next)_

# MASTER DOMAIN (12 tools)
_(to spec next)_

# OTHER — primitives / scheduled / external (send_and_await_reply, run_cutoff_batch, call_maps_route, delegate_write)
_(to spec next)_
