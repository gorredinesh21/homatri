# 🙋‍♂️ Master Specification: The Customer Agent Tools

This document outlines the complete persona, communication rules, categories, and **11 Final Production LLM Tool Specifications** for the **Customer Agent** in Homaatri, complete with **Left-to-Right Execution Flowcharts (`graph LR`)** for all cross-domain linked tools.

---

## 🎭 1. Persona & Communication Rules
* **Human Persona**: Warm, welcoming, respectful, efficient, and deeply attentive. Acts as a personal dining concierge on WhatsApp.
* **Tone**: Polite, encouraging, and clear. Responds in simple English / Hinglish based on the customer's greeting.
* **Core Rule**: Never hallucinates dish prices or payment URLs, strictly enforces meal cutoffs (12:00 PM Lunch / 7:00 PM Dinner), uses `customer_phone` for 100% WhatsApp alignment, and relies on 100% programmatic billing in Python.

---

## 🛠️ 2. Production Toolset Specification (11 Tools)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          CUSTOMER AGENT TOOLSET                             │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │
 ┌─────────────────┬─────────────────┬─┴───────────────┬─────────────────┬─────────────────┐
 ▼                 ▼                 ▼                 ▼                 ▼                 ▼
CAT 1: PROFILE &   CAT 2: KITCHEN    CAT 3: CART &     CAT 4: UNIFIED    CAT 5: LIVE SUPPORT
LOCATION ONBOARDING DISCOVERY & MENUS CUTOFF CHECK      PAYMENT LINKS     RECEIPTS & REVIEWS
• Profile Tool     • Find Nearby     • Atomic Init &   • Unified Payment • Active Status
• Register Profile • View Chef Menu    Order Tool      Link Tool         • Order History
• Location Pin                       • Add Extra Item  (Initial/Topup)   • Submit Review
```

---

### 📍 CATEGORY 1: Profile & Location Onboarding

#### Tool 1: `get_customer_profile_tool`
* **Linking Status**: `STANDALONE_INTERNAL`
* **Purpose**: Identifies the customer on incoming WhatsApp webhooks.
* **Inputs**:
  - `customer_phone`: `str` (Required)
* **Expected Output Structure**:
  ```json
  {
    "customer_phone": "+919876543210",
    "name": "Dinesh",
    "delivery_address": "Flat 301, Hitech City, Hyderabad",
    "latitude": 17.4482938,
    "longitude": 78.3814841,
    "is_registered": true
  }
  ```
* **DB Read**: `SELECT * FROM customer_profiles WHERE phone_number = ?` (Read)

---

#### Tool 2: `register_customer_profile_tool` *(REFINED 2-STEP ONBOARDING)*
* **Linking Status**: `STANDALONE_INTERNAL`
* **Purpose**: Saves first-time customer's name and text address, and prompts them on WhatsApp to send a location pin attachment.
* **Inputs**:
  - `customer_phone`: `str` (Required)
  - `name`: `str` (Required)
  - `delivery_address`: `str` (Required)
* **Expected Output Structure**:
  ```json
  {
    "customer_phone": "+919876543210",
    "name": "Dinesh",
    "delivery_address": "Flat 301, Hitech City, Hyderabad",
    "status": "PENDING_LOCATION_PIN",
    "prompt_message": "Profile created! Please tap the attachment clip on WhatsApp to share your location pin."
  }
  ```
* **DB Write**: `INSERT INTO customer_profiles ...` (Write)

---

#### Tool 3: `update_customer_location_pin_tool`
* **Linking Status**: `STANDALONE_INTERNAL`
* **Purpose**: Updates customer GPS coordinates when they tap "Share Location" attachment pin on WhatsApp.
* **Inputs**:
  - `customer_phone`: `str` (Required)
  - `latitude`: `float` (Required)
  - `longitude`: `float` (Required)
  - `address_text`: `str | None` (Optional)
* **Expected Output Structure**:
  ```json
  {
    "customer_phone": "+919876543210",
    "latitude": 17.4482938,
    "longitude": 78.3814841,
    "status": "LOCATION_UPDATED_SUCCESSFULLY"
  }
  ```
* **DB Write**: `UPDATE customer_profiles SET latitude = ?, longitude = ? ...` (Write)

---

### 🍽️ CATEGORY 2: Kitchen Discovery & Menu Browsing

#### Tool 4: `find_nearby_home_kitchens_tool`
* **Linking Status**: `STANDALONE_INTERNAL` (Cross-Domain Read)
* **Purpose**: Fetches active home kitchens sorted from closest to farthest from the customer's location using Haversine distance math.
* **Inputs**:
  - `customer_phone`: `str` (Required)
  - `meal_window`: `str` (Required, `'LUNCH'` or `'DINNER'`)
* **Expected Output Structure**:
  ```json
  [
    {
      "chef_phone": "+919876543210",
      "kitchen_name": "Ramesh Home Kitchen",
      "distance_km": 1.4,
      "specialty": "North Indian Thalis",
      "available_dishes_count": 3
    }
  ]
  ```
* **DB Read**: Global Read across `chef_profiles`, `chef_menu_items`, `chef_daily_inventory`. (Read)

---

#### Tool 5: `view_chef_menu_tool`
* **Linking Status**: `STANDALONE_INTERNAL` (Cross-Domain Read)
* **Purpose**: Displays full dish catalog, descriptions, prices, and remaining batch inventory for a selected home kitchen.
* **Inputs**:
  - `chef_phone`: `str` (Required)
  - `meal_window`: `str` (Required, `'LUNCH'` or `'DINNER'`)
* **Expected Output Structure**:
  ```json
  {
    "kitchen_name": "Ramesh Home Kitchen",
    "chef_phone": "+919876543210",
    "meal_window": "LUNCH",
    "menu_items": [
      {
        "menu_item_id": "item_201",
        "dish_name": "Special Paneer Thali",
        "unit_price": 180.00,
        "remaining_inventory": 7,
        "is_available": true
      }
    ]
  }
  ```
* **DB Read**: Global Read on `chef_menu_items` & `chef_daily_inventory`. (Read)

---

### 🛒 CATEGORY 3: Cart Creation & Cutoff Validation

#### Tool 6: `initialize_customer_order_tool` *(ATOMIC 1-STEP ORDER CREATION)*
* **Linking Status**: `CROSS_AGENT_LINKED`
* **Purpose**: Checks system cutoff clock ($\le$ 12:00 PM for Lunch, $\le$ 7:00 PM for Dinner), creates order header, and atomically appends initial dishes into `customer_order_items` in a single turn.
* **Inputs**:
  - `customer_phone`: `str` (Required)
  - `chef_phone`: `str` (Required)
  - `meal_window`: `str` (Required)
  - `items`: `list[dict]` (Required)
* **Execution Flowchart**:
  ```mermaid
  graph LR
      Customer["Customer Agent<br>(initialize_customer_order_tool)"] -->|Check Cutoff Payload| Master["Master Agent<br>(validate_meal_cutoff_clock_tool)"]
      Master -->|Cutoff Validated| DB["PostgreSQL<br>(Insert Header & Items)"]
      DB -->|Draft Cart Ready| END["END (Order Header Created)"]
  ```
* **Expected Output Structure**:
  ```json
  {
    "order_id": "ord_104",
    "customer_phone": "+919876543210",
    "chef_phone": "+919876543210",
    "meal_window": "LUNCH",
    "status": "PENDING_PAYMENT",
    "items_added": [
      { "dish_name": "Special Paneer Thali", "quantity": 2, "unit_price": 180.00, "item_subtotal": 360.00 }
    ],
    "cart_subtotal": 360.00,
    "cutoff_check": "PASSED_BEFORE_12PM"
  }
  ```
* **DB Write**: `INSERT INTO customer_orders` & `INSERT INTO customer_order_items` (Write)

---

#### Tool 7: `add_item_to_order_tool`
* **Linking Status**: `STANDALONE_INTERNAL`
* **Purpose**: Used when a customer subsequently adds extra dishes to an existing order in a later turn.
* **Inputs**:
  - `customer_phone`: `str` (Required)
  - `order_id`: `str` (Required)
  - `menu_item_id`: `str` (Required)
  - `quantity`: `int` (Required)
  - `special_instructions`: `str | None` (Optional)
* **Expected Output Structure**:
  ```json
  {
    "order_id": "ord_104",
    "item_added": "Gulab Jamun",
    "quantity": 1,
    "unit_price": 40.00,
    "item_subtotal": 40.00,
    "updated_cart_total": 400.00,
    "status": "ITEM_ADDED"
  }
  ```
* **DB Write**: `INSERT INTO customer_order_items ...` (Write)

---

### 💳 CATEGORY 4: Unified Payment Links

#### Tool 8: `generate_payment_link_tool` *(UNIFIED BILLING TOOL)*
* **Linking Status**: `CROSS_AGENT_LINKED`
* **Purpose**: Programmatically calculates unpaid balance (initial bill `subtotal + delivery_fee` OR mid-cooking top-up) and generates UPI payment link URL.
* **Inputs**:
  - `customer_phone`: `str` (Required)
  - `order_id`: `str` (Required)
* **Execution Flowchart**:
  ```mermaid
  graph LR
      CustAgent["Customer Agent<br>(generate_payment_link_tool)"] -->|Calculate Bill| Master["Master Agent<br>(Generate UPI Link)"]
      Master -->|Send Payment Link| Customer["Customer<br>(Pays via UPI)"]
      Customer -->|Webhook POST| Razorpay["Razorpay/Stripe Webhook"]
      Razorpay -->|Payment Paid Payload| MasterWebhook["Master Agent<br>(process_payment_gateway_webhook_tool)"]
      MasterWebhook -->|Update Status = CONFIRMED| END["END (Order Confirmed)"]
  ```
* **Expected Output Structure**:
  ```json
  {
    "order_id": "ord_104",
    "payment_id": "pay_901",
    "payment_type": "INITIAL",
    "amount_due": 390.00,
    "payment_link_url": "https://pay.homatri.com/pay_901",
    "status": "PENDING_PAYMENT"
  }
  ```
* **DB Write**: `INSERT INTO customer_payments ...` (Write)

---

### 📞 CATEGORY 5: Live Support, Receipts & Reviews

#### Tool 9: `get_active_order_status_tool`
* **Linking Status**: `CROSS_AGENT_LINKED` (Read-Only Bridge)
* **Purpose**: Answers live support queries on WhatsApp (*"Where is my driver?"*, *"Is my lunch cooking?"*).
* **Inputs**:
  - `customer_phone`: `str` (Required)
* **Execution Flowchart**:
  ```mermaid
  graph LR
      CustAgent["Customer Agent<br>(get_active_order_status_tool)"] -->|Query Operational State| Master["Master Agent"]
      Master -->|Join Tables| DB["customer_orders + chef_readiness + driver_locations"]
      DB -->|Live Tracking Summary| CustAgent
      CustAgent --> END["END (Format WhatsApp Reply)"]
  ```
* **Expected Output Structure**:
  ```json
  {
    "customer_phone": "+919876543210",
    "order_id": "ord_104",
    "order_status": "PICKED_UP",
    "chef_name": "Ramesh Home Kitchen",
    "driver_name": "Vikram",
    "driver_phone": "+919988776655",
    "delivery_stop": "Apartment Gate Drop-Off at My Home Bhooja",
    "estimated_arrival_time": "1:15 PM"
  }
  ```
* **DB Read**: Global Read across `customer_orders`, `chef_order_readiness`, `driver_locations`. (Read)

---

#### Tool 10: `get_order_history_tool`
* **Linking Status**: `STANDALONE_INTERNAL`
* **Purpose**: Retrieves past order receipts for a customer.
* **Inputs**:
  - `customer_phone`: `str` (Required)
* **Expected Output Structure**:
  ```json
  [
    {
      "order_id": "ord_104",
      "date": "2026-07-28",
      "chef_name": "Ramesh Home Kitchen",
      "total_paid": 390.00,
      "dishes": ["2x Special Paneer Thali"],
      "status": "DELIVERED"
    }
  ]
  ```
* **DB Read**: `SELECT * FROM customer_orders WHERE customer_id = ? AND status = 'DELIVERED'` (Read)

---

#### Tool 11: `submit_order_review_tool`
* **Linking Status**: `STANDALONE_INTERNAL`
* **Purpose**: Records post-delivery 1–5 star reviews for the home chef and delivery driver.
* **Inputs**:
  - `customer_phone`: `str` (Required)
  - `order_id`: `str` (Required)
  - `chef_rating`: `int` (Required)
  - `driver_rating`: `int` (Required)
  - `review_text`: `str | None` (Optional)
* **Expected Output Structure**:
  ```json
  {
    "order_id": "ord_104",
    "chef_rating": 5,
    "driver_rating": 5,
    "status": "REVIEW_SAVED",
    "thank_you_message": "Thank you for reviewing your meal!"
  }
  ```
* **DB Write**: `INSERT INTO customer_reviews ...` (Write)

---

## 🗄️ 3. Table Design & Query Access Map (Milestone 1)

Derived bottom-up from the tools above. Full standalone version: [`customer_tables.md`](customer_tables.md).

> **Tool 3 folded into `register`:** onboarding is a 2-phase HITL flow (write details → `interrupt()` for location pin → save coords → `ACTIVE`). So **11 → 10 tools**.

### Base Rules Applied (all agents)
1. **Phone is the natural key** (`customer_phone`); transactional rows keep `VARCHAR(36)` ids.
2. **Write invariant** — writes only `customer_*`; cross-domain writes = HANDOFF to Master.
3. **Reads are global**; cross-domain reads finalized under owner.
4. **Master delegates cross-domain writes**; writes INTO customer domain land here as executors (DW1/DW2 below).
5. **`driver_locations` dropped**; **payment via Master** (Master owns gateway; two LangGraph interrupts); **`units_sold` derived**; **order/item snapshots**; **avg ratings derived**.

### Query List
| Q | Tool | R/W | What it does | Table(s) | Filter/key | Index | On list? |
|---|---|---|---|---|---|---|---|
| Q1 | get_profile | R | identify customer (HOT) | `customer_profiles` | customer_phone | PK(customer_phone) | ✅ |
| Q2 | register *(phase 1)* | W | insert profile (name/address, `is_registered=false`) | `customer_profiles` | customer_phone | PK | ✅ own |
| — | register | PAUSE | `interrupt()` "share location"; if/else validates (→ `system_hitl_sessions`) | — | — | — | ⚑ note |
| Q3 | register *(phase 2)* | W | save coords + `is_registered=true` | `customer_profiles` | customer_phone | PK | ✅ own |
| Q4 | find_nearby | R | get customer's coords | `customer_profiles` | customer_phone | PK | ✅ own |
| Q5 | find_nearby | R (x-domain) | active kitchens + availability (Haversine app; remaining derived) | `chef_profiles` ⋈ `chef_menu_items` ⋈ `chef_daily_inventory` | active_status; meal_type,is_available; service_date | chef_profiles(active_status); chef_menu_items(chef_phone,meal_type,is_available); chef_daily_inventory(chef_phone,service_date) | ✅ (Chef) |
| Q6 | view_menu | R (x-domain) | dishes + remaining inventory for chef+meal | `chef_menu_items` ⋈ `chef_daily_inventory` (+ SUM items) | chef_phone, meal_type | chef_menu_items(chef_phone,meal_type) | ✅ (Chef+own) |
| Q7 | init_order | R (x-domain) | is cutoff open? (direct read) | `system_meal_windows` (+`system_settings`) | service_date, meal_type | UNIQUE(service_date,meal_type) | ✅ (System) |
| Q8 | init_order | R (x-domain+own) | validate items: price + dish_name + availability + remaining capacity (max_cap − SUM sold) | `chef_menu_items` ⋈ `chef_daily_inventory` (+ SUM items) | menu_item_id; chef_phone, service_date | chef_menu_items(PK); chef_daily_inventory(chef_phone,menu_item_id,service_date) | ✅ (Chef+own) |
| Q9 | init_order | W | insert order header (snapshot `kitchen_name`, PENDING_PAYMENT) | `customer_orders` | order_id | PK | ✅ own |
| Q10 | init_order | W | insert line items (snapshot dish_name, unit_price, service_date) | `customer_order_items` | order_id | (order_id) | ✅ own |
| Q11 | add_item | R | order editable & customer's (pre-cond) | `customer_orders` | order_id, customer_phone | PK | ✅ own |
| Q12 | add_item | R (x-domain+own) | validate item: price + remaining capacity (derived) | `chef_menu_items` ⋈ `chef_daily_inventory` (+ SUM items) | menu_item_id; chef_phone, service_date | as Q8 | ✅ (Chef+own) |
| Q13 | add_item | W | append line item (snapshot) | `customer_order_items` | order_id | (order_id) | ✅ own |
| Q14 | payment_link | R | read order+items → final amount | `customer_orders` ⋈ `customer_order_items` | order_id | PK; (order_id) | ✅ own |
| Q15 | payment_link | R (x-domain) | delivery_fee / config | `system_settings` | key | PK(key) | ✅ (System) |
| Q16 | payment_link | R | prior payments (top-up detect) | `customer_payments` | order_id | (order_id) | ✅ own |
| — | payment_link | HANDOFF | send final amount → Master mints UPI link via gateway → returns link | → Master (owns gateway) | — | — | ⚑ note |
| Q17 | payment_link | W | insert `customer_payments` (PENDING+link) — Customer executor, delegated by Master | `customer_payments` | order_id | (order_id) | ✅ own (delegated) |
| — | payment_link | PAUSE | Customer `interrupt()` `PAYMENT_AWAIT_MASTER_APPROVAL`; resumes on Master approval → notify user | — | — | — | ⚑ note |
| Q18 | active_status | R | customer's active order | `customer_orders` | customer_phone, status | (customer_phone, status) | ✅ own |
| Q19 | active_status | R (x-domain) | live status: readiness + route + stop + driver | `chef_order_readiness` ⋈ `system_delivery_stops` ⋈ `system_delivery_routes` ⋈ `driver_profiles` | order_id; stop→route→driver | stops(target_ref_id,stop_type) | ✅ (Chef/System/Rider) |
| Q20 | order_history | R | past delivered + dishes (snapshots → no chef join) | `customer_orders` ⋈ `customer_order_items` | customer_phone, status=DELIVERED | (customer_phone, status) | ✅ own |
| Q21 | submit_review | R | verify order delivered & customer's (pre-cond) | `customer_orders` | order_id | PK | ✅ own |
| Q22 | submit_review | W | insert review | `customer_reviews` | order_id | (order_id) | ✅ own |

### Delegated-Write Executors (customer domain, triggered by Master)
| DW | Executor | W | Table | Trigger |
|---|---|---|---|---|
| DW1 | `execute_order_status_transition` | W | `customer_orders` | cutoff (BATCHED), Chef (PACKED), Driver (PICKED_UP/DELIVERED), payment (CONFIRMED), cancel (CANCELLED) |
| DW2 | `execute_payment_status_update` | W | `customer_payments` | payment webhook (PAID/FAILED/REFUNDED) |

### Customer-Owned Tables (6)
| Table | Columns | Keys / Indexes |
|---|---|---|
| `customer_profiles` | customer_phone (PK), name, delivery_address, latitude `DECIMAL(10,8)`, longitude `DECIMAL(11,8)`, is_registered, created_at, updated_at | PK(customer_phone) |
| `customer_orders` | order_id (PK `VARCHAR(36)`), customer_phone (FK), chef_phone (FK), meal_window `meal_window_enum`, service_date `DATE`, status `order_status_enum`, cart_subtotal, delivery_fee, total_amount `DECIMAL(10,2)`, kitchen_name (snapshot), created_at, updated_at | PK; (customer_phone, status); (chef_phone, meal_window, service_date, status) |
| `customer_order_items` | item_id (PK), order_id (FK), menu_item_id (FK), chef_phone (denorm), service_date (snapshot), dish_name (snapshot), quantity, unit_price `DECIMAL(10,2)` (snapshot), item_subtotal, special_instructions, created_at | (order_id); (chef_phone, menu_item_id, service_date); (menu_item_id) |
| `customer_payments` | payment_id (PK), order_id (FK), customer_phone (FK), payment_type `payment_type_enum`, amount_due `DECIMAL(10,2)`, payment_link_url, gateway, gateway_payment_id, transaction_id, status `payment_status_enum`, created_at, paid_at | (order_id); (customer_phone); (status) |
| `customer_reviews` | review_id (PK), order_id (FK), customer_phone (FK), chef_phone, driver_phone, chef_rating (1-5), driver_rating (1-5), review_text, created_at | (order_id); (chef_phone); (driver_phone) |
| ~~`customer_chat_history`~~ | superseded → unified runtime-written `conversation_messages` | — |

**New enums:** `order_status_enum`, `payment_status_enum`, `payment_type_enum`.

**Cross-domain reads → owners:** chef_* (Chef); system_* (System); driver_profiles (Rider). **Handoffs → Master:** payment link mint; inbound payment webhook (DW1/DW2). **New HITL interrupt types:** `AWAIT_LOCATION_PIN`, `PAYMENT_AWAIT_MASTER_APPROVAL`.
