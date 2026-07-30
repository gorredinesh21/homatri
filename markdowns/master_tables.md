# 👑 Master Specification: Master / System Entity Tables & Column Schemas

This document contains the 100% finalized production SQL column schemas, data types, constraints, default values, foreign keys, indexes, and usage rationale for the **Master / System Domain** (Entity 4).

---

## 🧭 Base Architectural Rules & Category Breakdown
1. **Master Ownership**: Master owns and writes directly **ONLY** to `system_*` tables. All subagent domain writes are **DELEGATED** to target executors (Customer DW1/DW2).
2. **Unified Chat History**: Replaced per-domain chat history tables with the shared, runtime-written, insert-only `conversation_messages` table.
3. **4 Functional Categories**:
   - **Category 1**: Batch & Cutoff Orchestration (3 Tables)
   - **Category 2**: GCP Route Solver & Stop Dispatch (3 Tables)
   - **Category 3**: System Resilience, HITL & Webhooks (3 Tables)
   - **Category 4**: Communications & Runtime Infrastructure (2 Tables)

---

## 🗄️ Master / System Domain Tables (11 Tables)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                       MASTER / SYSTEM 11-TABLE DOMAIN                       │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │
  ┌───────────────────┬────────────────┴───┬───────────────────┬──────────────┐
  ▼                   ▼                    ▼                   ▼              ▼
CAT 1: BATCH &      CAT 2: GCP ROUTE     CAT 3: HITL,        CAT 4: COMMUNICATIONS
CUTOFF CLOCK        & STOP DISPATCH      WEBHOOKS & LOGS     & RUNTIME CHAT
(3 Tables)          (3 Tables)           (3 Tables)          (2 Tables)
```

---

## 📂 CATEGORY 1: Batch & Cutoff Orchestration (3 Tables)

### Table 1: `system_meal_windows` (Meal Window Status)
* **Primary Key**: `window_id` (`VARCHAR(36)` - UUID)
* **Unique Constraint**: `UNIQUE(service_date, meal_type)`
* **Purpose**: State tracker for daily Lunch & Dinner meal windows. Controls window opening, cutoff locks (12:00 PM for Lunch, 7:00 PM for Dinner), and batch processing status (`OPEN` $\rightarrow$ `LOCKED_PROCESSING` $\rightarrow$ `COMPLETED`).

| # | Column Name | Data Type | Nullable? | Default Value | Foreign Key / Index | Description & Usage Rationale |
|---|---|---|---|---|---|---|
| 1 | **`window_id`** | `VARCHAR(36)` | `NOT NULL` | *UUID* | `PRIMARY KEY` | Unique surrogate ID for this meal window instance. |
| 2 | **`service_date`** | `DATE` | `NOT NULL` | *None* | B-Tree Index | Service date (e.g. `2026-07-31`). |
| 3 | **`meal_type`** | `VARCHAR(20)` | `NOT NULL` | `'LUNCH'` | B-Tree Index | `'LUNCH'` or `'DINNER'`. |
| 4 | **`cutoff_at`** | `TIMESTAMPTZ` | `NOT NULL` | *None* | *None* | Hard cutoff timestamp (e.g. `12:00:00+05:30`). |
| 5 | **`status`** | `VARCHAR(30)` | `NOT NULL` | `'OPEN'` | B-Tree Index | Window status: `'OPEN'`, `'LOCKED_PROCESSING'`, `'COMPLETED'`. |
| 6 | **`total_confirmed_orders`**| `INTEGER` | `NOT NULL` | `0` | *None* | Count of confirmed orders locked in this batch. |
| 7 | **`total_revenue`** | `DECIMAL(10, 2)` | `YES` | `0.00` | *None* | Total order revenue collected for this batch. |
| 8 | **`locked_at`** | `TIMESTAMPTZ` | `YES` | `NULL` | *None* | Timestamp when 12 PM / 7 PM cutoff cron locked window. |
| 9 | **`completed_at`** | `TIMESTAMPTZ` | `YES` | `NULL` | *None* | Timestamp when all deliveries for batch finished. |
| 10 | **`created_at`** | `TIMESTAMPTZ` | `NOT NULL` | `CURRENT_TIMESTAMP` | *None* | Record creation timestamp. |
| 11 | **`updated_at`** | `TIMESTAMPTZ` | `NOT NULL` | `CURRENT_TIMESTAMP` | *None* | Last update timestamp. |

* **Indexes**: `PRIMARY KEY (window_id)`, `UNIQUE CONSTRAINT unique_date_meal (service_date, meal_type)`, `INDEX idx_window_status (status)`.

---

### Table 2: `system_settings` (Global Platform Config)
* **Primary Key**: `key` (`VARCHAR(50)`)
* **Purpose**: Key-value configuration store for system-wide business rules, cutoff times, delivery fees, search radii, timezones, and feature flags.

| # | Column Name | Data Type | Nullable? | Default Value | Foreign Key / Index | Description & Usage Rationale |
|---|---|---|---|---|---|---|
| 1 | **`key`** | `VARCHAR(50)` | `NOT NULL` | *None* | `PRIMARY KEY` | Setting identifier (e.g. `'delivery_fee'`, `'lunch_cutoff_time'`). |
| 2 | **`value`** | `JSONB` | `NOT NULL` | *None* | *None* | Flexible JSON value payload e.g. `{"amount": 30.00}`. |
| 3 | **`category`** | `VARCHAR(50)` | `NOT NULL` | `'GENERAL'` | B-Tree Index | Grouping category e.g. `'BILLING'`, `'CUTOFF'`, `'GEOGRAPHY'`. |
| 4 | **`description`** | `TEXT` | `YES` | `NULL` | *None* | Human-readable explanation of setting. |
| 5 | **`is_public`** | `BOOLEAN` | `NOT NULL` | `false` | *None* | True if readable by public subagent tools. |
| 6 | **`updated_by_admin_id`**| `VARCHAR(36)`| `YES` | `NULL` | `FK(admin_users)` | Admin user ID who last modified this setting. |
| 7 | **`updated_at`** | `TIMESTAMPTZ` | `NOT NULL` | `CURRENT_TIMESTAMP` | *None* | Timestamp of last config update. |

* **Indexes**: `PRIMARY KEY (key)`, `INDEX idx_settings_category (category)`.

---

### Table 3: `system_route_optimization_runs` (GCP Observability Audit)
* **Primary Key**: `run_id` (`VARCHAR(36)` - UUID)
* **Foreign Key**: `window_id` $\rightarrow$ `system_meal_windows(window_id)`
* **Purpose**: Audit ledger for every GCP Route Optimization API call executed during 12 PM / 7 PM batch cutoffs. Stores raw request/response payloads, latency, cost, and optimization metrics.

| # | Column Name | Data Type | Nullable? | Default Value | Foreign Key / Index | Description & Usage Rationale |
|---|---|---|---|---|---|---|
| 1 | **`run_id`** | `VARCHAR(36)` | `NOT NULL` | *UUID* | `PRIMARY KEY` | Unique surrogate ID for this GCP API run. |
| 2 | **`window_id`** | `VARCHAR(36)` | `NOT NULL` | `FK(system_meal_windows)`, B-Tree Index | Meal window instance optimized by this run. |
| 3 | **`service_date`** | `DATE` | `NOT NULL` | *None* | B-Tree Index | Service date (e.g. `2026-07-31`). |
| 4 | **`meal_window`** | `VARCHAR(20)` | `NOT NULL` | `'LUNCH'` | *None* | `'LUNCH'` or `'DINNER'`. |
| 5 | **`total_input_shipments`**| `INTEGER` | `NOT NULL` | *None* | *None* | Number of customer orders submitted to GCP. |
| 6 | **`total_input_vehicles`** | `INTEGER` | `NOT NULL` | *None* | *None* | Number of active drivers submitted to GCP. |
| 7 | **`total_routes_generated`**| `INTEGER` | `NOT NULL` | *None* | *None* | Number of optimized routes returned by GCP. |
| 8 | **`raw_request_payload`**| `JSONB` | `NOT NULL` | *None* | *None* | Complete JSON request body sent to GCP. |
| 9 | **`raw_response_payload`**| `JSONB` | `NOT NULL` | *None* | *None* | Complete JSON response body returned by GCP. |
| 10 | **`api_latency_ms`** | `INTEGER` | `NOT NULL` | *None* | *None* | Execution latency in milliseconds. |
| 11 | **`estimated_cost_usd`**| `DECIMAL(10, 4)` | `YES` | `0.0000` | *None* | Estimated GCP API bill cost for this run. |
| 12 | **`status`** | `VARCHAR(20)` | `NOT NULL` | `'SUCCESS'` | B-Tree Index | Status: `'SUCCESS'`, `'FAILED'`, `'PARTIAL'`. |
| 13 | **`error_detail`** | `TEXT` | `YES` | `NULL` | *None* | Error message if GCP API failed. |
| 14 | **`executed_at`** | `TIMESTAMPTZ` | `NOT NULL` | `CURRENT_TIMESTAMP` | *None* | Exact execution timestamp. |

* **Indexes**: `PRIMARY KEY (run_id)`, `INDEX idx_route_runs_window (window_id)`.

---

## 📂 CATEGORY 2: GCP Route Solver & Stop Dispatch (3 Tables)

### Table 4: `system_delivery_routes` (Master Driver Routes)
* **Primary Key**: `route_id` (`VARCHAR(36)` - UUID)
* **Foreign Key**: `window_id` $\rightarrow$ `system_meal_windows(window_id)`, `driver_phone` $\rightarrow$ `driver_profiles(driver_phone)`
* **Purpose**: Master delivery route itinerary generated by GCP Route Optimization API at 12 PM / 7 PM cutoff. Assigned to a specific delivery driver for a specific meal window.

| # | Column Name | Data Type | Nullable? | Default Value | Foreign Key / Index | Description & Usage Rationale |
|---|---|---|---|---|---|---|
| 1 | **`route_id`** | `VARCHAR(36)` | `NOT NULL` | *UUID* | `PRIMARY KEY` | Unique surrogate ID for this delivery route (e.g. `rt_801`). |
| 2 | **`window_id`** | `VARCHAR(36)` | `NOT NULL` | `FK(system_meal_windows)`, B-Tree Index | Meal window instance this route belongs to. |
| 3 | **`driver_phone`** | `VARCHAR(15)` | `NOT NULL` | `FK(driver_profiles)`, B-Tree Index | Delivery driver assigned to execute this route. |
| 4 | **`service_date`** | `DATE` | `NOT NULL` | *None* | B-Tree Index | Service date (e.g. `2026-07-31`). |
| 5 | **`meal_window`** | `VARCHAR(20)` | `NOT NULL` | `'LUNCH'` | B-Tree Index | `'LUNCH'` or `'DINNER'`. |
| 6 | **`total_stops`** | `INTEGER` | `NOT NULL` | *None* | *None* | Total pickup + apartment gate drop-off stops. |
| 7 | **`total_orders`** | `INTEGER` | `NOT NULL` | *None* | *None* | Total customer orders delivered on this route. |
| 8 | **`total_distance_km`**| `DECIMAL(10, 2)` | `YES` | `0.00` | *None* | Total route distance calculated by GCP. |
| 9 | **`estimated_duration_mins`**| `INTEGER` | `YES` | `NULL` | *None* | Total estimated travel + stop duration in minutes. |
| 10 | **`status`** | `VARCHAR(20)` | `NOT NULL` | `'ASSIGNED'` | B-Tree Index | Route status: `'ASSIGNED'`, `'IN_PROGRESS'`, `'COMPLETED'`. |
| 11 | **`encoded_polyline`**| `TEXT` | `YES` | `NULL` | *None* | Encoded Google Maps polyline string (for future map view rendering). |
| 12 | **`optimized_at`** | `TIMESTAMPTZ` | `NOT NULL` | `CURRENT_TIMESTAMP` | *None* | Timestamp when GCP generated route. |
| 13 | **`created_at`** | `TIMESTAMPTZ` | `NOT NULL` | `CURRENT_TIMESTAMP` | *None* | Record creation timestamp. |
| 14 | **`updated_at`** | `TIMESTAMPTZ` | `NOT NULL` | `CURRENT_TIMESTAMP` | *None* | Last update timestamp. |

* **Indexes**: `PRIMARY KEY (route_id)`, `INDEX idx_routes_driver (driver_phone, service_date, meal_window)`.

---

### Table 5: `system_delivery_stops` (Pickup & Gate Drop-Off Stops)
* **Primary Key**: `stop_id` (`VARCHAR(36)` - UUID)
* **Foreign Key**: `route_id` $\rightarrow$ `system_delivery_routes(route_id)`
* **Purpose**: Sequenced stop list for a route (`stop_index`: 1, 2, 3...). Represents both home kitchen pickups (`PICKUP_KITCHEN`) and consolidated apartment gate drop-offs (`DROPOFF_GATE`). Dispatches single-leg Google Maps navigation links (`Stop N ➔ Stop N+1`).

| # | Column Name | Data Type | Nullable? | Default Value | Foreign Key / Index | Description & Usage Rationale |
|---|---|---|---|---|---|---|
| 1 | **`stop_id`** | `VARCHAR(36)` | `NOT NULL` | *UUID* | `PRIMARY KEY` | Unique surrogate ID for this stop. |
| 2 | **`route_id`** | `VARCHAR(36)` | `NOT NULL` | `FK(system_delivery_routes)`, B-Tree Index | Route this stop belongs to. |
| 3 | **`stop_index`** | `INTEGER` | `NOT NULL` | *None* | B-Tree Index | Stop sequence order number (`1`, `2`, `3`...). |
| 4 | **`stop_type`** | `VARCHAR(20)` | `NOT NULL` | `'DROPOFF_GATE'` | B-Tree Index | `'PICKUP_KITCHEN'` or `'DROPOFF_GATE'`. |
| 5 | **`target_ref_id`** | `VARCHAR(100)`| `NOT NULL` | *None* | B-Tree Index | Target identifier (`chef_phone` for kitchen, or `apartment_name` for gate). |
| 6 | **`location_name`** | `VARCHAR(100)`| `NOT NULL` | *None* | *None* | Display name (e.g. "Ramesh Kitchen" or "My Home Bhooja Gate 2"). |
| 7 | **`address`** | `TEXT` | `NOT NULL` | *None* | *None* | Full physical address of stop. |
| 8 | **`latitude`** | `DECIMAL(10, 8)` | `NOT NULL` | *None* | *None* | GPS Latitude of stop location. |
| 9 | **`longitude`** | `DECIMAL(11, 8)` | `NOT NULL` | *None* | *None* | GPS Longitude of stop location. |
| 10 | **`single_leg_maps_url`**| `TEXT` | `YES` | `NULL` | *None* | Pre-generated Google Maps navigation URL for this leg (`Stop N-1 ➔ Stop N`). |
| 11 | **`estimated_arrival`**| `TIMESTAMPTZ` | `NOT NULL` | *None* | *None* | Estimated arrival timestamp calculated by GCP. |
| 12 | **`actual_arrival`** | `TIMESTAMPTZ` | `YES` | `NULL` | *None* | Actual arrival timestamp recorded when driver taps "Reached". |
| 13 | **`status`** | `VARCHAR(20)` | `NOT NULL` | `'PENDING'` | B-Tree Index | Stop status: `'PENDING'`, `'ARRIVED'`, `'COMPLETED'`. |
| 14 | **`created_at`** | `TIMESTAMPTZ` | `NOT NULL` | `CURRENT_TIMESTAMP` | *None* | Stop creation timestamp. |
| 15 | **`updated_at`** | `TIMESTAMPTZ` | `NOT NULL` | `CURRENT_TIMESTAMP` | *None* | Last update timestamp. |

* **Indexes**: `PRIMARY KEY (stop_id)`, `UNIQUE CONSTRAINT unique_route_stop (route_id, stop_index)`, `INDEX idx_stops_target (target_ref_id, stop_type)`.

---

### Table 6: `system_delivery_stop_orders` (Stop↔Order Junction)
* **Primary Key**: `PRIMARY KEY (stop_id, order_id)`
* **Foreign Key**: `stop_id` $\rightarrow$ `system_delivery_stops(stop_id)`, `order_id` $\rightarrow$ `customer_orders(order_id)`
* **Purpose**: Junction table mapping delivery stops to customer order IDs. Handles consolidated apartment gate drop-offs (where 1 security gate stop delivers 5 customer orders).

| # | Column Name | Data Type | Nullable? | Default Value | Foreign Key / Index | Description & Usage Rationale |
|---|---|---|---|---|---|---|
| 1 | **`stop_id`** | `VARCHAR(36)` | `NOT NULL` | `FK(system_delivery_stops)`, B-Tree Index | Delivery stop ID. |
| 2 | **`order_id`** | `VARCHAR(36)` | `NOT NULL` | `FK(customer_orders)`, B-Tree Index | Customer order ID delivered at this stop. |
| 3 | **`created_at`** | `TIMESTAMPTZ` | `NOT NULL` | `CURRENT_TIMESTAMP` | *None* | Association creation timestamp. |

* **Indexes**: `PRIMARY KEY (stop_id, order_id)`, `INDEX idx_stop_orders_order (order_id)`.

---

## 📂 CATEGORY 3: System Resilience, HITL & Webhooks (3 Tables)

### Table 7: `system_hitl_sessions` (LangGraph Interrupt Checkpoints)
* **Primary Key**: `session_id` (`VARCHAR(36)` - UUID)
* **Purpose**: State persistence ledger for LangGraph Human-In-The-Loop (HITL) `interrupt()` checkpoints. Freezes state execution during async human waits (e.g. Chef dietary counter-offers, Driver unlocatable address pin requests, Customer onboarding location pin, Payment webhooks). Includes a 15-minute TTL.

| # | Column Name | Data Type | Nullable? | Default Value | Foreign Key / Index | Description & Usage Rationale |
|---|---|---|---|---|---|---|
| 1 | **`session_id`** | `VARCHAR(36)` | `NOT NULL` | *UUID* | `PRIMARY KEY` | Unique surrogate ID for this HITL interrupt session. |
| 2 | **`thread_id`** | `VARCHAR(100)`| `NOT NULL` | B-Tree Index | LangGraph thread checkpoint ID. |
| 3 | **`interrupt_type`** | `VARCHAR(50)` | `NOT NULL` | *None* | B-Tree Index | Interrupt type: `'DIETARY_APPROVAL'`, `'CANCELLATION_APPROVAL'`, `'UNLOCATABLE_ADDRESS'`, `'AWAIT_LOCATION_PIN'`, `'PAYMENT_AWAIT_PROVIDER'`, `'PAYMENT_AWAIT_MASTER_APPROVAL'`. |
| 4 | **`waiting_on_role`**| `VARCHAR(20)` | `NOT NULL` | *None* | *None* | Role holding up execution: `'CHEF'`, `'CUSTOMER'`, `'DRIVER'`, `'PROVIDER'`. |
| 5 | **`waiting_on_phone`**| `VARCHAR(15)` | `YES` | `NULL` | B-Tree Index | Phone number of human whose reply will resume graph. |
| 6 | **`order_id`** | `VARCHAR(36)` | `YES` | `NULL` | B-Tree Index | Associated order ID. |
| 7 | **`payload`** | `JSONB` | `NOT NULL` | `'{}'` | *None* | Frozen state data payload passed to human on WhatsApp. |
| 8 | **`default_on_expiry`**| `JSONB` | `YES` | `'{}'` | *None* | Fallback payload if 15-minute TTL expires without reply. |
| 9 | **`status`** | `VARCHAR(20)` | `NOT NULL` | `'WAITING'` | B-Tree Index | Session status: `'WAITING'`, `'RESUMED'`, `'EXPIRED'`, `'RESOLVED'`. |
| 10 | **`expires_at`** | `TIMESTAMPTZ` | `NOT NULL` | *None* | Partial Index | Hard expiration timestamp (Default: `created_at + 15 mins`). |
| 11 | **`resolved_at`** | `TIMESTAMPTZ` | `YES` | `NULL` | *None* | Timestamp when human replied or session expired. |
| 12 | **`created_at`** | `TIMESTAMPTZ` | `NOT NULL` | `CURRENT_TIMESTAMP` | *None* | Session creation timestamp. |

* **Indexes**: `PRIMARY KEY (session_id)`, `PARTIAL INDEX idx_hitl_waiting (expires_at) WHERE status = 'WAITING'`.

---

### Table 8: `system_payment_webhook_events` (Webhook Idempotency)
* **Primary Key**: `event_id` (`VARCHAR(36)` - UUID)
* **Unique Constraint**: `UNIQUE(gateway_event_id)`
* **Purpose**: Idempotency & verification ledger for incoming payment gateway webhooks (Razorpay / Stripe / PhonePe). Prevents duplicate payment processing by enforcing unique gateway event IDs and storing HMAC signature verification results.

| # | Column Name | Data Type | Nullable? | Default Value | Foreign Key / Index | Description & Usage Rationale |
|---|---|---|---|---|---|---|
| 1 | **`event_id`** | `VARCHAR(36)` | `NOT NULL` | *UUID* | `PRIMARY KEY` | Unique surrogate ID for this webhook callback. |
| 2 | **`gateway`** | `VARCHAR(50)` | `NOT NULL` | `'RAZORPAY'` | *None* | Gateway vendor: `'RAZORPAY'`, `'STRIPE'`, `'PHONEPE'`. |
| 3 | **`gateway_event_id`**| `VARCHAR(100)`| `NOT NULL` | `UNIQUE` | Unique B-Tree Index | Provider's unique event ID for 100% idempotency dedup. |
| 4 | **`event_type`** | `VARCHAR(50)` | `NOT NULL` | *None* | *None* | Provider event type e.g. `'payment.captured'`. |
| 5 | **`payment_id`** | `VARCHAR(36)` | `YES` | `NULL` | B-Tree Index | Associated `customer_payments.payment_id`. |
| 6 | **`order_id`** | `VARCHAR(36)` | `YES` | `NULL` | B-Tree Index | Associated `customer_orders.order_id`. |
| 7 | **`signature_verified`**| `BOOLEAN` | `NOT NULL` | `false` | *None* | True if HMAC-SHA256 signature verification passed. |
| 8 | **`raw_payload`** | `JSONB` | `NOT NULL` | *None* | *None* | Complete raw JSON payload received from payment gateway. |
| 9 | **`processing_status`**| `VARCHAR(20)`| `NOT NULL` | `'RECEIVED'`| B-Tree Index | Status: `'RECEIVED'`, `'PROCESSED'`, `'FAILED'`, `'DUPLICATE'`. |
| 10 | **`error_detail`** | `TEXT` | `YES` | `NULL` | *None* | Error description if HMAC or processing failed. |
| 11 | **`received_at`** | `TIMESTAMPTZ` | `NOT NULL` | `CURRENT_TIMESTAMP` | *None* | Webhook arrival timestamp. |
| 12 | **`processed_at`** | `TIMESTAMPTZ` | `YES` | `NULL` | *None* | Timestamp when Master Agent delegated DW1/DW2 status update. |

* **Indexes**: `PRIMARY KEY (event_id)`, `UNIQUE INDEX (gateway_event_id)`.

---

### Table 9: `system_agent_logs` (System Audit & Write Delegation Log)
* **Primary Key**: `log_id` (`VARCHAR(36)` - UUID)
* **Purpose**: System-wide audit trail for multi-agent events, subagent handoffs, write delegations (`delegate_cross_domain_write_tool`), security exceptions, and cutoff locks.

| # | Column Name | Data Type | Nullable? | Default Value | Foreign Key / Index | Description & Usage Rationale |
|---|---|---|---|---|---|---|
| 1 | **`log_id`** | `VARCHAR(36)` | `NOT NULL` | *UUID* | `PRIMARY KEY` | Unique surrogate ID for this audit entry. |
| 2 | **`event_type`** | `VARCHAR(50)` | `NOT NULL` | *None* | B-Tree Index | Event type e.g. `'CUTOFF_LOCK'`, `'WRITE_DELEGATION'`, `'SECURITY_ALERT'`. |
| 3 | **`source_role`** | `VARCHAR(20)` | `NOT NULL` | *None* | *None* | Initiating agent: `'CUSTOMER'`, `'CHEF'`, `'DRIVER'`, `'MASTER'`, `'ADMIN'`, `'SYSTEM'`. |
| 4 | **`target_role`** | `VARCHAR(20)` | `YES` | `NULL` | *None* | Receiving agent for handoffs/delegations. |
| 5 | **`order_id`** | `VARCHAR(36)` | `YES` | `NULL` | B-Tree Index | Associated order ID (if applicable). |
| 6 | **`payload`** | `JSONB` | `YES` | `'{}'` | *None* | Contextual event data payload. |
| 7 | **`severity`** | `VARCHAR(20)` | `NOT NULL` | `'INFO'` | Partial Index | Log severity: `'INFO'`, `'WARNING'`, `'CRITICAL'`. |
| 8 | **`created_at`** | `TIMESTAMPTZ` | `NOT NULL` | `CURRENT_TIMESTAMP` | B-Tree Index | Log timestamp. |

* **Indexes**: `PRIMARY KEY (log_id)`, `INDEX idx_logs_order (order_id, created_at)`.

---

## 📂 CATEGORY 4: Communications & Runtime Infrastructure (2 Tables)

### Table 10: `system_outbound_queue` (WhatsApp Message Dispatcher)
* **Primary Key**: `message_id` (`VARCHAR(36)` - UUID)
* **Purpose**: Outbound message queue for WhatsApp communications sent via Meta WhatsApp Business Cloud API. Master Agent and system dispatchers insert queued messages here; background worker processes queue and handles retries.

| # | Column Name | Data Type | Nullable? | Default Value | Foreign Key / Index | Description & Usage Rationale |
|---|---|---|---|---|---|---|
| 1 | **`message_id`** | `VARCHAR(36)` | `NOT NULL` | *UUID* | `PRIMARY KEY` | Unique surrogate ID for this queued message. |
| 2 | **`recipient_phone`** | `VARCHAR(15)` | `NOT NULL` | B-Tree Index | Recipient's normalized phone number (E.164 format). |
| 3 | **`recipient_role`** | `VARCHAR(20)` | `NOT NULL` | *None* | *None* | Role of recipient: `'CUSTOMER'`, `'CHEF'`, `'DRIVER'`. |
| 4 | **`message_text`** | `TEXT` | `NOT NULL` | *None* | *None* | Outbound message text content. |
| 5 | **`message_type`** | `VARCHAR(30)` | `NOT NULL` | `'TEXT'` | *None* | Type: `'TEXT'`, `'LOCATION_REQUEST'`, `'INTERACTIVE_BUTTON'`, `'TEMPLATE'`. |
| 6 | **`template_name`** | `VARCHAR(100)`| `YES` | `NULL` | *None* | Meta approved WhatsApp template name (if applicable). |
| 7 | **`wa_message_id`** | `VARCHAR(100)`| `YES` | `NULL` | B-Tree Index | Meta API returned message ID (e.g. `wamid.HBgL...`). |
| 8 | **`status`** | `VARCHAR(20)` | `NOT NULL` | `'QUEUED'` | Partial Index | Status: `'QUEUED'`, `'SENT'`, `'DELIVERED'`, `'READ'`, `'FAILED'`. |
| 9 | **`attempts`** | `INTEGER` | `NOT NULL` | `0` | *None* | Number of API dispatch retry attempts. |
| 10 | **`error_detail`** | `TEXT` | `YES` | `NULL` | *None* | Error details if Meta Cloud API failed. |
| 11 | **`related_order_id`**| `VARCHAR(36)`| `YES` | `NULL` | B-Tree Index | Associated order ID (if applicable). |
| 12 | **`created_at`** | `TIMESTAMPTZ` | `NOT NULL` | `CURRENT_TIMESTAMP` | *None* | Message enqueue timestamp. |
| 13 | **`sent_at`** | `TIMESTAMPTZ` | `YES` | `NULL` | *None* | Timestamp when Meta API confirmed message sent. |

* **Indexes**: `PRIMARY KEY (message_id)`, `PARTIAL INDEX idx_outbound_queued (created_at) WHERE status = 'QUEUED'`.

---

### Table 11: `conversation_messages` (Unified Shared Runtime Chat Log)
* **Primary Key**: `message_id` (`VARCHAR(36)` - UUID)
* **Unique Constraint**: `UNIQUE(wa_message_id)`
* **Purpose**: Unified, **INSERT-ONLY** (no update/delete) conversation ledger for ALL incoming and outgoing WhatsApp messages across Customers, Chefs, and Drivers. Written automatically by messaging runtime. Read by Context Assembler to fetch last 4-5 messages before each LLM turn.

| # | Column Name | Data Type | Nullable? | Default Value | Foreign Key / Index | Description & Usage Rationale |
|---|---|---|---|---|---|---|
| 1 | **`message_id`** | `VARCHAR(36)` | `NOT NULL` | *UUID* | `PRIMARY KEY` | Unique surrogate ID for this message log entry. |
| 2 | **`phone`** | `VARCHAR(15)` | `NOT NULL` | B-Tree Index | User's normalized phone number (E.164 format). |
| 3 | **`actor_role`** | `VARCHAR(20)` | `NOT NULL` | *None* | B-Tree Index | Role of user: `'CUSTOMER'`, `'CHEF'`, `'DRIVER'`. |
| 4 | **`direction`** | `VARCHAR(10)` | `NOT NULL` | *None* | *None* | Direction: `'INBOUND'` (User $\rightarrow$ Agent) or `'OUTBOUND'` (Agent $\rightarrow$ User). |
| 5 | **`source`** | `VARCHAR(30)` | `NOT NULL` | *None* | *None* | Source entity: `'USER'`, `'CUSTOMER_AGENT'`, `'CHEF_AGENT'`, `'DRIVER_AGENT'`, `'MASTER_AGENT'`, `'SYSTEM'`. |
| 6 | **`message_type`** | `VARCHAR(30)` | `NOT NULL` | `'TEXT'` | *None* | `'TEXT'`, `'LOCATION'`, `'INTERACTIVE'`, `'IMAGE'`, `'TEMPLATE'`. |
| 7 | **`message_text`** | `TEXT` | `YES` | `NULL` | *None* | Text content of WhatsApp message. |
| 8 | **`latitude`** | `DECIMAL(10, 8)` | `YES` | `NULL` | *None* | GPS Latitude (if LOCATION attachment pin). |
| 9 | **`longitude`** | `DECIMAL(11, 8)` | `YES` | `NULL` | *None* | GPS Longitude (if LOCATION attachment pin). |
| 10 | **`media_ref`** | `TEXT` | `YES` | `NULL` | *None* | WhatsApp media ID / URL if message contains media. |
| 11 | **`related_order_id`**| `VARCHAR(36)`| `YES` | `NULL` | B-Tree Index | Associated order ID (if applicable). |
| 12 | **`wa_message_id`** | `VARCHAR(100)`| `YES` | `NULL` | Unique B-Tree Index | Meta WhatsApp message ID for **100% inbound webhook deduplication**. |
| 13 | **`raw_payload`** | `JSONB` | `YES` | `'{}'` | *None* | Full raw JSON webhook payload from Meta. |
| 14 | **`created_at`** | `TIMESTAMPTZ` | `NOT NULL` | `CURRENT_TIMESTAMP` | B-Tree Index | Message timestamp. |

* **Indexes**: `PRIMARY KEY (message_id)`, `INDEX idx_chat_context_fetch (phone, created_at DESC)`, `UNIQUE INDEX (wa_message_id)`.
