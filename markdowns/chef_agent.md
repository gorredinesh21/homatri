# 👨‍🍳 Master Specification: The Chef Agent

This document outlines the complete persona, communication rules, categories, toolset, and guardrails for the **Chef Agent** in Homaatri.

---

## 🎭 1. Persona & Communication Rules
* **Human Persona**: Operations-focused, precise, time-conscious, and respectful. Speaks like an efficient Head Chef / Kitchen Manager communicating with home cooks on WhatsApp.
* **Tone**: Direct, encouraging, clear, and structured. Uses bulleted checklists for easy reading in hot kitchens.
* **Core Rule**: Admin handles onboarding & menu pricing in DB. The Chef Agent never alters customer billing, strictly delivers consolidated order checklists at 12:00 PM (Lunch) and 7:00 PM (Dinner), and never writes directly to Customer or Driver tables.

---

## 🛠️ 2. Chef Agent Action & Toolset (10 Actions)

### 📍 CATEGORY 1: Kitchen Profile Lookup

#### 1. `get_chef_profile(phone_number)`
* **Purpose**: Identifies the onboarded home-cook chef when they message on WhatsApp.
* **Inputs**: `phone_number: str` (E.164 format)
* **Outputs**: `dict | None` (Chef profile details or `None` if phone is not an authorized chef).
* **DB Operation**: `SELECT * FROM chef_profiles WHERE phone_number = ?` (Read)

---

### 📦 CATEGORY 2: Daily Capacity & Inventory Control

#### 2. `set_daily_batch_capacity(chef_id, menu_item_id, date, max_capacity)`
* **Purpose**: Sets max prep limit for a dish for a specific date (e.g. *"Max 15 Paneer Thalis for Lunch on July 28"*).
* **Inputs**: `chef_id: str`, `menu_item_id: str`, `date: str`, `max_capacity: int`
* **Outputs**: `dict` (`inventory_id`, capacity status).
* **DB Operation**: `INSERT INTO chef_daily_inventory ...` (Write - `chef_daily_inventory`)

#### 3. `toggle_dish_stock_status(chef_id, menu_item_id, is_available)`
* **Purpose**: Instantly marks a dish as OUT_OF_STOCK or IN_STOCK mid-day when ingredients run out.
* **Inputs**: `chef_id: str`, `menu_item_id: str`, `is_available: bool`
* **Outputs**: `dict` (Updated stock status).
* **DB Operation**: `UPDATE chef_menu_items SET is_available = ? WHERE id = ?` (Write - `chef_menu_items`)

#### 4. `get_chef_inventory_status(chef_id, date)`
* **Purpose**: Displays remaining meal prep capacity for today's batch.
* **Inputs**: `chef_id: str`, `date: str`
* **Outputs**: `list[dict]` (Dishes, max_capacity, units_sold, remaining_slots).
* **DB Operation**: `SELECT * FROM chef_daily_inventory WHERE chef_id = ? AND date = ?` (Read)

---

### 🍳 CATEGORY 3: Cutoff Batch Dispatch & Prep Tracking

#### 5. `get_chef_batch_checklist(chef_id, meal_window, date)`
* **Purpose**: Triggered automatically at cutoff (12:00 PM / 7:00 PM) to deliver the formatted meal prep checklist on WhatsApp.
* **Inputs**: `chef_id: str`, `meal_window: str` (`'LUNCH'` or `'DINNER'`), `date: str`
* **Outputs**: `dict` (Total meal count, dish breakdowns, customer order IDs).
* **DB Operation**: Global Read across `customer_orders` & `customer_order_items`. (Read)

#### 6. `get_batch_item_totals(chef_id, meal_window, date)`
* **Purpose**: Provides quick cooking summary counts (e.g. *"Cook 12 Paneer Thalis, 6 Veg Thalis"*).
* **Inputs**: `chef_id: str`, `meal_window: str`, `date: str`
* **Outputs**: `dict` (Summarized dish counts for immediate cooking).
* **DB Operation**: Global Read on `customer_order_items`. (Read)

---

### 📦 CATEGORY 4: Packing & Readiness Signals

#### 7. `mark_order_packed_ready(order_id, chef_id)`
* **Purpose**: Broadcasts readiness signal when chef finishes packing food (*"Order #104 is ready"*). Triggers notification to Master Agent $\rightarrow$ Driver Agent.
* **Inputs**: `order_id: str`, `chef_id: str`
* **Outputs**: `dict` (`order_id`, `status: PACKED_READY`, timestamp).
* **DB Operation**: `INSERT INTO chef_order_readiness` & `UPDATE customer_orders SET status = 'PACKED'` (Write - `chef_*`)

#### 8. `get_assigned_driver_for_pickup(chef_id, meal_window, date)`
* **Purpose**: Answers chef's query (*"Which driver is picking up my batch?"*).
* **Inputs**: `chef_id: str`, `meal_window: str`, `date: str`
* **Outputs**: `dict` (Driver name, vehicle info, contact number, ETA).
* **DB Operation**: Global Read across `system_delivery_stops` & `driver_profiles`. (Read)

---

### 🤝 CATEGORY 5: HITL & Interaction Management (Customer & Rider Events)

#### 9. `handle_customer_dietary_request_approval(order_id, chef_id, approval_decision)`
* **Purpose**: Resumes LangGraph execution when a chef approves/declines a mid-prep dietary request (e.g. *"No garlic for Order #104"*).
* **Inputs**: `order_id: str`, `chef_id: str`, `approval_decision: bool` (`True` = Approved, `False` = Declined)
* **Outputs**: `dict` (Resumes LangGraph checkpoint & notifies Master Agent $\rightarrow$ Customer Agent).
* **DB Operation**: Updates LangGraph State Checkpointer & logs to `chef_chat_history`.

#### 10. `notify_chef_driver_arrival(order_id, chef_id, driver_name, arrival_time)`
* **Purpose**: Alert sent to chef on WhatsApp when driver arrives at kitchen door (*"Driver Vikram has arrived to pick up Order #104"*).
* **Inputs**: `order_id: str`, `chef_id: str`, `driver_name: str`, `arrival_time: str`
* **Outputs**: `dict` (WhatsApp notification status).
* **DB Operation**: `INSERT INTO chef_chat_history ...` (Write)
