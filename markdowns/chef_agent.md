# 👨‍🍳 Master Specification: The Chef Agent Tools

This document outlines the complete persona, communication rules, categories, and **9 Final Production LLM Tool Specifications** for the **Chef Agent** in Homaatri.

---

## 🎭 1. Persona & Communication Rules
* **Human Persona**: Operations-focused, precise, time-conscious, and respectful. Speaks like an efficient Head Chef / Kitchen Manager communicating with home cooks on WhatsApp.
* **Tone**: Direct, encouraging, clear, and structured. Uses bulleted checklists for easy reading in hot kitchens.
* **Core Rule**: Admin handles onboarding & menu pricing in DB. The Chef Agent never alters customer billing, strictly delivers consolidated order checklists at 12:00 PM (Lunch) and 7:00 PM (Dinner), and never writes directly to Customer or Driver tables.

---

## 🛠️ 2. Production Toolset Specification (9 Tools)

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                            CHEF AGENT TOOLSET                               │
└──────────────────────────────────────┬──────────────────────────────────────┘
                                       │
 ┌─────────────────┬─────────────────┬─┴───────────────┬─────────────────┬─────────────────┐
 ▼                 ▼                 ▼                 ▼                 ▼                 ▼
CAT 1: KITCHEN     CAT 2: CAPACITY   CAT 3: BATCH      CAT 4: PACKING    CAT 5: HITL &
PROFILE LOOKUP     & INVENTORY       DISPATCH          & READINESS       INTERACTION MGMT
• Profile Tool     • Capacity Tool   • Merged Checklist• Mark Packed     • Counter-Offer
                   • Stock Toggle      & Totals Tool   • Driver Info     • Driver Arrival
                   • Inventory Check                                       Status
```

---

### 📍 CATEGORY 1: Kitchen Profile Lookup

#### Tool 1: `get_chef_profile_tool`
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
* **DB Write**: `INSERT INTO chef_daily_inventory ...` (Write - `chef_daily_inventory`)

---

#### Tool 3: `toggle_dish_stock_tool`
* **Action Source**: Action 3 (`toggle_dish_stock_status`)
* **Purpose**: Instantly marks a dish as IN_STOCK or OUT_OF_STOCK mid-day when ingredients run out.
* **Inputs**:
  - `chef_phone`: `str` (Required)
  - `menu_item_id`: `str` (Required)
  - `is_available`: `bool` (Required, `True` for IN_STOCK, `False` for OUT_OF_STOCK)
* **Expected Output Structure**:
  ```json
  {
    "menu_item_id": "item_201",
    "dish_name": "Paneer Thali",
    "is_available": false,
    "status": "UPDATED_OUT_OF_STOCK"
  }
  ```
* **DB Write**: `UPDATE chef_menu_items SET is_available = ? WHERE id = ?` (Write - `chef_menu_items`)

---

#### Tool 4: `check_daily_inventory_status_tool`
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
* **Action Source**: Merges Action 5 (`get_chef_batch_checklist`) + Action 6 (`get_batch_item_totals`)
* **Purpose**: Called at cutoff (12:00 PM / 7:00 PM) to generate summary cooking totals (*"Cook 12 Paneer Thalis, 6 Veg Thalis"*) AND itemized order checklists in a single call.
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
* **Action Source**: Action 7 (`mark_order_packed_ready`)
* **Purpose**: Broadcasts readiness signal when chef finishes packing food (*"Order #104 is ready"*). Notifies Master Agent $\rightarrow$ Driver Agent.
* **Inputs**:
  - `chef_phone`: `str` (Required)
  - `order_id`: `str` (Required)
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
* **DB Write**: `INSERT INTO chef_order_readiness` & `UPDATE customer_orders SET status = 'PACKED'` (Write - `chef_*`)

---

#### Tool 7: `get_assigned_driver_info_tool`
* **Action Source**: Action 8 (`get_assigned_driver_for_pickup`)
* **Purpose**: Answers chef's query (*"Which driver is picking up my batch?"* or *"What is the driver's phone number?"*).
* **Inputs**:
  - `chef_phone`: `str` (Required)
  - `meal_window`: `str` (Required, `'LUNCH'` or `'DINNER'`)
  - `date`: `str` (Required, `'YYYY-MM-DD'`)
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
* **Action Source**: Action 9 (`handle_customer_dietary_request_approval`)
* **Purpose**: Executed when a chef accepts, declines, or proposes an alternative counter-offer (e.g. *"Can provide 2 extra rotis instead of 3"*) to a customer's request.
* **Inputs**:
  - `chef_phone`: `str` (Required)
  - `order_id`: `str` (Required)
  - `decision`: `str` (Required, `ENUM('ACCEPTED', 'DECLINED', 'COUNTER_OFFER')`)
  - `counter_offer_text`: `str | None` (Optional, e.g. `"Can provide 2 extra rotis instead of 3"`)
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
* **Action Source**: Action 10 (`notify_chef_driver_arrival`)
* **Purpose**: Used when a chef asks *"Has the driver arrived outside my door yet?"* or *"Is Vikram here?"*.
* **Inputs**:
  - `chef_phone`: `str` (Required)
  - `date`: `str` (Required, `'YYYY-MM-DD'`)
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
