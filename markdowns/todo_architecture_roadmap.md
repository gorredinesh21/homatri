# 📋 Homaatri Pending Architecture & Implementation Roadmap

This document tracks the status of the **6 Key Architectural Milestones** required to complete the platform.

---

## 🗺️ Master Roadmap Status

```
 ┌─────────────────────────────────────────────────────────────────────────────────────────────────┐
 │                                   ARCHITECTURE ROADMAP STATUS                                   │
 └────────────────────────────────────────────────┬────────────────────────────────────────────────┘
                                                  │
  ┌──────────────┬──────────────┬─────────────────┴───┬──────────────┬──────────────┬──────────────┐
  ▼              ▼              ▼                     ▼              ▼              ▼              ▼
1. DB SCHEMA  2. AGENT TOOLS 3. LANGGRAPH STATE   4. WA WEBHOOK  5. PAYMENTS    6. ADMIN DASHBOARD
   [✅ 100%]    [✅ 100%]      [✅ 100%]             [✅ 100%]      [✅ 100%]      & WA CHAT PORTAL
                                                                                 [⏸️ ON HOLD]
```






---

## ✅ Milestone 1: Database Column Schemas & SQL DDL Definitions — COMPLETED

### Accomplishments:
- Finalized production SQL column schemas, data types, nullability, defaults, PK/FKs, and B-tree indexes across **all 24 tables and 6 entities**.
- Established **Prefixed UUID Primary Key Standard** (`ord_...`, `pay_...`, `itm_...`, `rt_...`, `stp_...`, `log_...`).
- Locked **4 Data Integrity & Security Implementation Guards** (Atomic SQL Transactions, Guard 2 Pre-Condition Assertion Matrix, Guard 3 Centralized Delegated Executors, Guard 4 Automated pytest suite).
- Documented in `markdowns/data_integrity_and_security_guards.md`, `markdowns/*_tables.md`, and `history.txt` (Section 27 & 28).


### Completed Accomplishments:
- Formally declared and mapped all backend actions into **40 Production LLM Tools (`@tool`)** across Chef (9), Customer (11), Master (12), and Driver (8) agents.
- Authored production blueprint file **[`all_40_agent_tools.py`](file:///home/dinesh/coding/PROJECTS/homatri/all_40_agent_tools.py)** declaring Pydantic input schemas and tool docstrings.
- Embedded standardized **Left-to-Right (`graph LR`) Mermaid Flowchart Diagrams** into all 4 agent master specification files in `markdowns/`.
- Enforced key architectural protocols:
  - **Counter-Offer Negotiation Protocol**: Chef Tool 8 (`respond_to_custom_request_tool`) with 3-way decision enum (`ACCEPTED`, `DECLINED`, `COUNTER_OFFER` + dynamic dish/qty payload).
  - **Atomic Cutoff Lock & GCP Route Solver**: Master Tool 2 (`execute_cutoff_batch_and_route_optimization_tool`) as an atomic service.
  - **2-Step Onboarding UX**: Customer Tool 2 (Text name/address) + Customer Tool 3 (WhatsApp location pin attachment).
  - **Atomic 1-Step Order Creation**: Customer Tool 6 (`initialize_customer_order_tool`) creating order header + items in 1 turn.
  - **Unified Payment Tool**: Customer Tool 8 (`generate_payment_link_tool`) auto-detecting initial bill vs top-up bill.
  - **Decoupled Handoffs**: LangGraph router edges handling state transitions without circular imports (`demo_langgraph_agents.py`).

---

## 🗄️ Milestone 1: Database Column Schemas & SQL DDL Definitions — NEXT UP

### Goal:
Finalize production-grade SQL DDL schemas for all **18 Master Database Tables** across Customer, Chef, Driver, and System domains.

### Detailed Requirements:
1. **Data Types & Precision**:
   - Primary Keys: `VARCHAR(36)` / `UUID` for non-sequential, secure IDs.
   - Monies & Financials: `DECIMAL(10, 2)` (never float).
   - Coordinates: `DECIMAL(10, 8)` for latitude, `DECIMAL(11, 8)` for longitude (PostGIS-compatible).
   - Timestamps: `TIMESTAMP WITH TIME ZONE` (`DEFAULT CURRENT_TIMESTAMP`).
2. **Enum Definitions**:
   - `meal_window_enum`: `['LUNCH', 'DINNER']`.
   - `order_status_enum`: `['PENDING_PAYMENT', 'CONFIRMED', 'BATCHED', 'COOKING', 'PACKED', 'PICKED_UP', 'DELIVERED', 'CANCELLED']`.
   - `payment_status_enum`: `['PENDING', 'PAID', 'FAILED', 'REFUNDED']`.
   - `payment_type_enum`: `['INITIAL', 'TOPUP', 'REFUND']`.
   - `stop_type_enum`: `['PICKUP_KITCHEN', 'DROPOFF_GATE']`.
3. **Foreign Key Constraints & Cascade Rules**:
   - Strict `FOREIGN KEY` definitions linking orders to profiles, line items to orders/chefs/dishes, and stops to routes.
   - `ON DELETE RESTRICT` to prevent accidental deletion of historical order receipts.
4. **Indexing Strategy**:
   - B-Tree indexes on `phone_number` across profiles for 3ms lookup.
   - Composite index on `(customer_id, status)` and `(chef_id, meal_window, date)`.

---

## 🕸️ Milestone 3: LangGraph State Machine & Graph Structure

### Goal:
Define the complete multi-agent graph topology, nodes, conditional edges, and state checkpointing.

### Detailed Requirements:
1. **State Schema (`TypedDict`)**:
   - Define `HomaatriGraphState`: `messages`, `sender_phone`, `current_role`, `active_order_id`, `is_interrupted`, `checkpoint_data`.
2. **Graph Node Definitions**:
   - `MasterNode`, `CustomerNode`, `ChefNode`, `DriverNode`.
3. **Conditional Routing Edges**:
   - Write edge functions evaluating subagent intent payloads to route control (`CustomerNode` ➔ `MasterNode` ➔ `ChefNode`).
4. **Checkpointer & `interrupt()` Configuration**:
   - Configure PostgreSQL checkpointer for human-in-the-loop state persistence.

---

## 📱 Milestone 4: WhatsApp Business API Webhook Gateway Engine

### Goal:
Design the FastAPI webhook ingress engine that receives, verifies, and dispatches WhatsApp messages.

### Detailed Requirements:
1. **FastAPI Webhook Verification Handshake**:
   - Implement `GET /webhook` to handle Meta's `hub.verify_token` and `hub.challenge` verification handshake.
2. **Incoming JSON Payload Parser**:
   - Implement `POST /webhook` to parse incoming text messages, WhatsApp location pin payloads, and interactive button clicks.
3. **Outbound WhatsApp Message Queue & Dispatcher**:
   - Build background worker calling Meta WhatsApp Business API (`https://graph.facebook.com/v18.0/.../messages`).

---

## 💳 Milestone 5: Payment Gateway Webhook & Financial Verification Flow

### Goal:
Design the asynchronous payment verification and programmatic billing engine.

### Detailed Requirements:
1. **Payment Webhook Endpoint**:
   - Implement `POST /webhook/payment` (Razorpay / Stripe).
2. **Cryptographic Signature Verification**:
   - Validate HMAC-SHA256 signature to guarantee payment authenticity.
3. **Order Status Transition**:
   - Automatically transition orders from `PENDING_PAYMENT` ➔ `CONFIRMED` upon payment verification.
   - Handle incremental top-up payments for mid-cooking dish additions.

---

## 🖥️ Milestone 6: Admin Dashboard & WhatsApp Live Chat Viewer Portal

### Goal:
Build a full web administration dashboard allowing ops staff to manage platform profiles (Chefs & Delivery Drivers) and inspect complete real-time WhatsApp chat conversations.

### Detailed Requirements:
1. **Chef & Driver Management Portal**:
   - **Chefs Management**: Add new home chefs, edit kitchen details, set operating days, set dish portion capacities, and toggle kitchen availability.
   - **Driver Management**: Register delivery drivers, assign vehicle details, set shift status (`ON_DUTY` / `OFF_DUTY`), and view active batch delivery routes.
   - **Operations & Revenue Dashboard**: Real-time view of 12 PM / 7 PM cutoff windows, live order volume pipeline, and platform GMV revenue.
2. **WhatsApp-Style Live Chat Conversation Viewer**:
   - Real-time chat viewer UI connected to the `conversation_messages` unified chat ledger.
   - **Phone Lookup & Filtering**: Search or select any phone number (Customer, Chef, Driver).
   - **Full Transcript Display**: Render complete chronological chat streams in a familiar WhatsApp Web UI layout (showing inbound user messages, agent responses, system alert notices, and HITL prompt checkpoints).

