# 👨‍🍳 Master Specification: The Chef Agent Tools

This document outlines the complete persona, communication rules, categories, and **9 Final Production LLM Tool Specifications** for the **Chef Agent** in Homaatri, complete with **Left-to-Right Execution Flowcharts (`graph LR`)** for all cross-domain linked tools.

---

## 🎭 1. Persona & Communication Rules
* **Human Persona**: Operations-focused, precise, time-conscious, and respectful. Speaks like an efficient Head Chef / Kitchen Manager communicating with home cooks on WhatsApp.
* **Tone**: Direct, encouraging, clear, and structured. Uses bulleted checklists for easy reading in hot kitchens.
* **Core Rule**: Admin handles onboarding & menu pricing in DB. The Chef Agent never alters customer billing, strictly delivers consolidated order checklists at 12:00 PM (Lunch) and 7:00 PM (Dinner), and never writes directly to Customer or Driver tables.

---

## 🛠️ 2. Production Toolset Specification (9 Tools)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                       CHEF AGENT TOOL LINKING MAP                           │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │
  ┌───────────────────┬────────────────┴───┬───────────────────┬──────────────┐
  ▼                   ▼                    ▼                   ▼              ▼
STANDALONE TOOLS     STANDALONE TOOLS     LINKED TOOL (6)     LINKED TOOL (8) LINKED TOOL (9)
Tools 1, 2, 3, 4     Tool 5 (Checklist)   Packed Ready ➔      Counter-Offer   Driver Arrival ➔
Profile & Capacity   Consolidated Prep    Notifies Driver     ➔ Master ➔      Queries Master
Read/Write           Checklist            Agent via Master    Customer        Stop State
```

---

### 📍 CATEGORY 1: Kitchen Profile Lookup

#### Tool 1: `get_chef_profile_tool`
* **Linking Status**: `STANDALONE_INTERNAL`
* **Action Source**: Action 1 (`get_chef_profile`)
* **Purpose**: Identifies an onboarded home-cook chef when they send a message on WhatsApp.
* **Inputs**:
  - `chef_phone`: `str` (Required, E.164 format e.g. `"+919876543210"`)
* **Expected Output Structure**:
  ```json
  {
    "chef_id": "chf_101",
    "chef_phone": "+919876543210",
    "kitchen_name": "Ramesh Home Kitchen",
    "address": "Flat 402, Hitech City, Hyderabad",
    "latitude": 17.4482938,
    "longitude": 78.3814841,
    "active_status": true
  }
  ```
* **DB Read**: `SELECT * FROM chef_profiles WHERE phone_number = ?` (Read)

---

### 📦 CATEGORY 2: Daily Capacity & Inventory Control

#### Tool 2: `set_daily_dish_capacity_tool`
* **Linking Status**: `STANDALONE_INTERNAL`
* **Action Source**: Action 2 (`set_daily_batch_capacity`)
* **Purpose**: Sets the maximum prep limit for a dish for a specific date (e.g. *"Max 15 Paneer Thalis for Lunch today"*).
* **Inputs**:
  - `chef_phone`: `str` (Required)
  - `menu_item_id`: `str` (Required)
  - `date`: `str` (Required, `'YYYY-MM-DD'`)
  - `max_capacity`: `int` (Required, `Field(gt=0)`)
* **Expected Output Structure**:
  ```json
  {
    "inventory_id": "inv_501",
    "chef_phone": "+919876543210",
    "menu_item_id": "item_201",
    "dish_name": "Paneer Thali",
    "max_capacity": 15,
    "date": "2026-07-29",
    "status": "SUCCESS"
  }
  ```
* **DB Write**: `INSERT INTO chef_daily_inventory ...` (Write)

---

#### Tool 3: `toggle_dish_stock_tool`
* **Linking Status**: `STANDALONE_INTERNAL`
* **Action Source**: Action 3 (`toggle_dish_stock_status`)
* **Purpose**: Instantly marks a dish as IN_STOCK or OUT_OF_STOCK mid-day when ingredients run out.
* **Inputs**:
  - `chef_phone`: `str` (Required)
  - `menu_item_id`: `str` (Required)
  - `is_available`: `bool` (Required)
* **Expected Output Structure**:
  ```json
  {
    "menu_item_id": "item_201",
    "dish_name": "Paneer Thali",
    "is_available": false,
    "status": "UPDATED_OUT_OF_STOCK"
  }
  ```
* **DB Write**: `UPDATE chef_menu_items SET is_available = ? WHERE id = ?` (Write)

---

#### Tool 4: `check_daily_inventory_status_tool`
* **Linking Status**: `STANDALONE_INTERNAL`
* **Action Source**: Action 4 (`get_chef_inventory_status`)
* **Purpose**: Displays the chef's remaining meal prep capacity and units sold for today's batch.
* **Inputs**:
  - `chef_phone`: `str` (Required)
  - `date`: `str` (Required, `'YYYY-MM-DD'`)
* **Expected Output Structure**:
  ```json
  [
    { "dish_name": "Paneer Thali", "max_capacity": 15, "units_sold": 8, "remaining_slots": 7 },
    { "dish_name": "Veg Thali", "max_capacity": 10, "units_sold": 10, "remaining_slots": 0 }
  ]
  ```
* **DB Read**: `SELECT * FROM chef_daily_inventory WHERE chef_id = ? AND date = ?` (Read)

---

### 🍳 CATEGORY 3: Cutoff Batch Dispatch & Prep Tracking

#### Tool 5: `get_chef_batch_checklist_tool` *(MERGED TOOL)*
* **Linking Status**: `STANDALONE_INTERNAL` (Cross-Domain Read)
* **Action Source**: Merges Action 5 (`get_chef_batch_checklist`) + Action 6 (`get_batch_item_totals`)
* **Purpose**: Called at cutoff (12:00 PM / 7:00 PM) to generate summary cooking totals AND itemized order checklists in a single call.
* **Inputs**:
  - `chef_phone`: `str` (Required)
  - `meal_window`: `str` (Required, `'LUNCH'` or `'DINNER'`)
  - `date`: `str` (Required, `'YYYY-MM-DD'`)
* **Expected Output Structure**:
  ```json
  {
    "chef_phone": "+919876543210",
    "meal_window": "LUNCH",
    "date": "2026-07-29",
    "total_meals_to_cook": 18,
    "summary_counts": [
      { "dish_name": "Paneer Thali", "quantity_to_cook": 12 },
      { "dish_name": "Veg Thali", "quantity_to_cook": 6 }
    ],
    "itemized_orders": [
      { "order_id": "ord_101", "dishes": ["1x Paneer Thali"], "special_notes": "Less spicy" },
      { "order_id": "ord_104", "dishes": ["2x Paneer Thali", "1x Veg Thali"], "special_notes": "No garlic" }
    ]
  }
  ```
* **DB Read**: Global Read across `customer_orders` & `customer_order_items`. (Read)

---

### 📦 CATEGORY 4: Packing & Readiness Signals

#### Tool 6: `mark_order_packed_ready_tool`
* **Linking Status**: `CROSS_AGENT_LINKED`
* **Action Source**: Action 7 (`mark_order_packed_ready`)
* **Purpose**: Broadcasts readiness signal when chef finishes packing food (*"Order #104 is ready"*). Notifies Master Agent $\rightarrow$ Driver Agent.
* **Inputs**:
  - `chef_phone`: `str` (Required)
  - `order_id`: `str` (Required)
* **Execution Flowchart**:
  ```mermaid
  graph LR
      Chef["Chef Agent<br>(mark_order_packed_ready_tool)"] -->|ORDER_PACKED_READY Payload| Master["Master Agent<br>(relay_order_ready_to_driver_tool)"]
      Master -->|WhatsApp Notification| Driver["Driver Agent<br>(Assigned Driver)"]
      Driver -->|Update Stop Status| END["END (Ready for Pickup)"]
  ```
* **Expected Output Structure**:
  ```json
  {
    "order_id": "ord_104",
    "chef_phone": "+919876543210",
    "status": "PACKED_READY",
    "packed_at": "12:35 PM",
    "driver_notified": true,
    "assigned_driver_name": "Vikram"
  }
  ```
* **DB Write**: `INSERT INTO chef_order_readiness` & `UPDATE customer_orders SET status = 'PACKED'` (Write)

---

#### Tool 7: `get_assigned_driver_info_tool`
* **Linking Status**: `CROSS_AGENT_LINKED` (Read-Only Bridge)
* **Action Source**: Action 8 (`get_assigned_driver_for_pickup`)
* **Purpose**: Answers chef's query (*"Which driver is picking up my batch?"* or *"What is the driver's phone number?"*).
* **Inputs**:
  - `chef_phone`: `str` (Required)
  - `meal_window`: `str` (Required, `'LUNCH'` or `'DINNER'`)
  - `date`: `str` (Required, `'YYYY-MM-DD'`)
* **Execution Flowchart**:
  ```mermaid
  graph LR
      Chef["Chef Agent<br>(get_assigned_driver_info_tool)"] -->|Query Assigned Driver| Master["Master Agent"]
      Master -->|Join Tables| DB["system_delivery_stops + driver_profiles"]
      DB -->|Driver Details & ETA| Chef
      Chef --> END["END (Format WhatsApp Reply)"]
  ```
* **Expected Output Structure**:
  ```json
  {
    "driver_name": "Vikram",
    "vehicle_info": "Hero Splendor (TS 09 EQ 1234)",
    "driver_phone": "+919988776655",
    "pickup_eta": "12:45 PM",
    "pickup_status": "EN_ROUTE_TO_KITCHEN"
  }
  ```
* **DB Read**: Global Read across `system_delivery_stops` & `driver_profiles`. (Read)

---

### 🤝 CATEGORY 5: HITL & Interaction Management (Customer & Rider Events)

#### Tool 8: `respond_to_custom_request_tool` *(COUNTER-OFFER PROTOCOL)*
* **Linking Status**: `CROSS_AGENT_LINKED` (Double-HITL Relay Loop)
* **Action Source**: Action 9 (`handle_customer_dietary_request_approval`)
* **Purpose**: Executed when a chef accepts, declines, or proposes an alternative counter-offer (e.g. *"Can provide 2 extra rotis instead of 3"*) to a customer's request.
* **Inputs**:
  - `chef_phone`: `str` (Required)
  - `order_id`: `str` (Required)
  - `decision`: `str` (Required, `ENUM('ACCEPTED', 'DECLINED', 'COUNTER_OFFER')`)
  - `counter_offer_text`: `str | None` (Optional)
* **Execution Flowchart**:
  ```mermaid
  graph LR
      RealChef["Real Chef<br>(WhatsApp Reply)"] -->|COUNTER_OFFER Payload| ChefAgent["Chef Agent<br>(respond_to_custom_request_tool)"]
      ChefAgent -->|Relay Payload| Master["Master Agent"]
      Master -->|Prompt Counter-Offer| CustAgent["Customer Agent"]
      CustAgent -->|WhatsApp Message| Customer["Customer<br>(Reply YES / NO)"]
      Customer -->|YES / NO Response| END["END (Resolution)"]
  ```
* **Expected Output Structure**:
  ```json
  {
    "order_id": "ord_104",
    "chef_decision": "COUNTER_OFFER",
    "counter_offer_text": "Can provide 2 extra rotis instead of 3",
    "resumed_graph": true,
    "relay_status": "SENT_COUNTER_OFFER_TO_MASTER_AGENT",
    "status_message": "Counter-offer sent to Master Agent to relay to Customer."
  }
  ```
* **DB Operation**: Updates LangGraph Checkpointer State & logs to `chef_chat_history`.

---

#### Tool 9: `check_driver_arrival_status_tool`
* **Linking Status**: `CROSS_AGENT_LINKED` (Read-Only Bridge)
* **Action Source**: Action 10 (`notify_chef_driver_arrival`)
* **Purpose**: Used when a chef asks *"Has the driver arrived outside my door yet?"* or *"Is Vikram here?"*.
* **Inputs**:
  - `chef_phone`: `str` (Required)
  - `date`: `str` (Required, `'YYYY-MM-DD'`)
* **Execution Flowchart**:
  ```mermaid
  graph LR
      Chef["Chef Agent<br>(check_driver_arrival_status_tool)"] -->|Query Stop Arrival| Master["Master Agent"]
      Master -->|Check Arrival Timestamp| DB["system_delivery_stops"]
      DB -->|Arrival Status| Chef
      Chef --> END["END (Format WhatsApp Reply)"]
  ```
* **Expected Output Structure**:
  ```json
  {
    "driver_name": "Vikram",
    "has_arrived": true,
    "arrived_at": "12:42 PM",
    "orders_to_collect": ["ord_101", "ord_104"],
    "status": "DRIVER_WAITING_AT_DOOR"
  }
  ```
* **DB Read**: Global Read across `system_delivery_stops` & `driver_locations`. (Read)

---

## 🗄️ 3. Table Design & Query Access Map (Milestone 1)

Derived bottom-up from the 9 tools above. Full standalone version: [`chef_tables.md`](chef_tables.md).

### Base Rules Applied (hold for ALL agents)
1. **Phone is the natural key** — tables key on normalized `chef_phone`; no phone→id resolve. Transactional rows keep `VARCHAR(36)` surrogate ids.
2. **Write invariant** — a tool WRITES only its own `chef_*` tables; any cross-domain write is a **HANDOFF to Master** (note only).
3. **Reads are global** — a tool may READ any table; cross-domain reads stay on the list, finalized under the owner.
4. **Cross-agent calls = notes** — another agent's queries are covered under that agent.
5. **`driver_locations` dropped** — arrival = `system_delivery_stops.actual_arrival`.

### Query List
| Q | Tool | R/W | What it does | Table(s) | Filter/key cols | Index | On list? |
|---|---|---|---|---|---|---|---|
| Q1 | 1 get_chef_profile | R | identify chef (HOT) | `chef_profiles` | chef_phone | PK(chef_phone) | ✅ |
| Q2 | 2 set_capacity | R | dish name + ownership | `chef_menu_items` | menu_item_id, chef_phone | PK; (chef_phone) | ✅ |
| Q3 | 2 set_capacity | W | upsert daily cap | `chef_daily_inventory` | chef_phone, menu_item_id, service_date | UNIQUE(chef_phone,menu_item_id,service_date) | ✅ own |
| Q4 | 3 toggle_stock | R | ownership pre-cond | `chef_menu_items` | menu_item_id, chef_phone | PK; (chef_phone) | ✅ |
| Q5 | 3 toggle_stock | W | flip IN/OUT of stock | `chef_menu_items` | menu_item_id | PK | ✅ own |
| Q6 | 4 check_inventory | R | remaining cap + sold | `chef_daily_inventory` ⋈ `chef_menu_items` | chef_phone, service_date | (chef_phone, service_date) | ✅ |
| Q7 | 5 batch_checklist | R (x-domain) | orders+items for chef → totals+itemized+notes | `customer_orders` ⋈ `customer_order_items` | items.chef_phone; orders.meal_window,service_date,status | items(chef_phone); orders(status,meal_window,service_date) | ✅ (Customer) |
| Q8 | 6 mark_packed | R (x-domain) | pre-cond: order COOKING | `customer_orders` | order_id | PK | ✅ (Customer) |
| Q9 | 6 mark_packed | W | record readiness | `chef_order_readiness` | order_id, chef_phone | (order_id) | ✅ own |
| — | 6 mark_packed | HANDOFF | order→PACKED + notify driver | → Master `relay_order_ready_to_driver_tool` | — | — | ❌ note |
| Q10 | 7 get_assigned_driver | R (x-domain) | pickup stop → driver + ETA | `system_delivery_stops` ⋈ `system_delivery_routes` ⋈ `driver_profiles` | stop.target_ref_id=chef_phone, PICKUP_KITCHEN | stops(target_ref_id,stop_type) | ✅ (System/Rider) |
| Q11 | 8 counter_offer | — | reply logged to `conversation_messages` by runtime (not a chef write) | `conversation_messages` | phone | (phone, created_at DESC) | runtime |
| — | 8 counter_offer | HANDOFF | resume interrupt + relay to customer | → Master (`system_hitl_sessions` resume + counter-offer relay → Customer) | — | — | ❌ note |
| Q12 | 9 check_arrival | R (x-domain) | driver arrived at my pickup? | `system_delivery_stops` ⋈ `system_delivery_stop_orders` ⋈ `system_delivery_routes` ⋈ `driver_profiles` | stop.target_ref_id=chef_phone, PICKUP_KITCHEN, date | stops(target_ref_id,stop_type) | ✅ (System/Rider) |

### Chef-Owned Tables
| Table | Columns | Keys / Indexes |
|---|---|---|
| `chef_profiles` | chef_phone (PK), kitchen_name, address, latitude `DECIMAL(10,8)`, longitude `DECIMAL(11,8)`, active_status, created_at, updated_at | PK(chef_phone) |
| `chef_menu_items` | menu_item_id (PK `VARCHAR(36)`), chef_phone (FK), dish_name, description, unit_price `DECIMAL(10,2)`, meal_type `meal_window_enum`, is_available (default true), created_at, updated_at | PK; idx(chef_phone) |
| `chef_daily_inventory` | inventory_id (PK), chef_phone (FK), menu_item_id (FK), service_date `DATE`, max_capacity (>0), units_sold (0), created_at, updated_at | UNIQUE(chef_phone,menu_item_id,service_date) |
| `chef_order_readiness` | readiness_id (PK), order_id (FK), chef_phone (FK), status `readiness_status_enum`, packed_at, created_at | idx(order_id) |
| ~~`chef_chat_history`~~ | superseded → unified runtime-written `conversation_messages` | — |

**New enum:** `readiness_status_enum` = (PREPARING, PACKED_READY).


**Cross-domain reads noted → owners:** `customer_orders`/`customer_order_items` (Customer); `system_delivery_stops`/`system_delivery_stop_orders`/`system_delivery_routes` (System); `driver_profiles` (Rider).

**Handoffs → Master:** Tool 6 (order→PACKED + notify driver); Tool 8 (hitl resume + counter-offer relay).

---

## 🛡️ 4. Data Integrity & Write Executors (Chef Domain)

### Prefixed UUID Primary Keys:
* `chef_profiles` $\rightarrow$ `chef_phone` (`VARCHAR(15)` - Normalized phone natural key)
* `chef_menu_items` $\rightarrow$ `menu_item_id` (`VARCHAR(36)` - `itm_` + UUID)
* `chef_daily_inventory` $\rightarrow$ `inventory_id` (`VARCHAR(36)` - `inv_` + UUID)
* `chef_order_readiness` $\rightarrow$ `readiness_id` (`VARCHAR(36)` - `red_` + UUID)

### Guard 2 Pre-Condition Assertions:
1. `set_daily_dish_capacity_tool`: Assert `chef_phone` exists; assert `menu_item_id` exists; assert `menu_item.chef_phone == chef_phone` (Ownership); assert `max_capacity > 0`; assert `service_date >= CURRENT_DATE`.
2. `toggle_dish_stock_tool`: Assert `menu_item_id` exists; assert `menu_item.chef_phone == chef_phone` (Ownership).
3. `mark_order_packed_ready_tool`: Assert `order_id` exists; assert `order.chef_phone == chef_phone` (Ownership); assert `order.status` IN (`'CONFIRMED'`, `'BATCHED'`, `'COOKING'`).
4. `respond_to_custom_request_tool`: Assert `order_id` exists; assert `order.chef_phone == chef_phone` (Ownership); assert `system_hitl_session` exists with `status == 'WAITING'`; assert `decision` IN (`'ACCEPTED'`, `'DECLINED'`, `'COUNTER_OFFER'`).

### Chef Write Executors:
1. `execute_daily_capacity_upsert()` $\rightarrow$ `chef_daily_inventory`
2. `execute_dish_stock_toggle()` $\rightarrow$ `chef_menu_items`
3. `execute_order_readiness_record()` $\rightarrow$ `chef_order_readiness` (Order status update delegated to Customer DW1).

