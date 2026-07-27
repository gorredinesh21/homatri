# 📋 Homaatri Pending Architecture & Implementation Roadmap

This document outlines the **5 Pending Architectural Milestones** required to complete the system design before writing production backend code.

---

## 🗺️ Master Pending Roadmap Overview

```
 ┌─────────────────────────────────────────────────────────────────────────┐
 │                       PENDING ARCHITECTURE ROADMAP                      │
 └────────────────────────────────────┬────────────────────────────────────┘
                                      │
  ┌───────────────────┬───────────────┴───┬───────────────────┬───────────────────┐
  ▼                   ▼                   ▼                   ▼                   ▼
1. DB COLUMN SCHEMAS 2. LLM AGENT TOOLS  3. LANGGRAPH STATE  4. WHATSAPP WEBHOOK 5. PAYMENT & BILLING
   & SQL DDL DEFS       DESIGN & MAPPING    GRAPH STRUCTURE     GATEWAY ENGINE      INTEGRITY ENGINE
```

---

## 🗄️ Milestone 1: Database Column Schemas & SQL DDL Definitions

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
   - `stop_type_enum`: `['PICKUP_KITCHEN', 'DROPOFF_CUSTOMER']`.
3. **Foreign Key Constraints & Cascade Rules**:
   - Strict `FOREIGN KEY` definitions linking orders to profiles, line items to orders/chefs/dishes, and stops to routes.
   - `ON DELETE RESTRICT` to prevent accidental deletion of historical order receipts.
4. **Indexing Strategy**:
   - B-Tree indexes on `phone_number` across profiles for 3ms lookup.
   - Composite index on `(customer_id, status)` and `(chef_id, meal_window, date)`.

---

## 🧰 Milestone 2: Agent Tools Design & Pydantic Mapping (Actions $\rightarrow$ Tools)

### Goal:
Translate primitive backend actions into high-level, production-ready `@tool` definitions for LangGraph.

### Detailed Requirements:
1. **Tool Combination & Splitting**:
   - Evaluate whether actions should be merged (e.g. `add_extra_items_mid_order` + `generate_topup_payment_link`) or split for LLM simplicity.
2. **Pydantic Input Schemas**:
   - Define strict input classes for every tool (e.g. `CreateOrderInput`, `ReportUnlocatableAddressInput`).
   - Include regex validators for phone numbers and positive integer bounds for quantities (`Field(gt=0)`).
3. **LLM Tool Docstring Engineering**:
   - Write clear, unambiguous docstrings for Gemini 3.6 Flash explaining *when* and *why* to invoke each tool.

---

## 🕸️ Milestone 3: LangGraph State Machine & Graph Structure

### Goal:
Define the complete multi-agent graph graph topology, nodes, conditional edges, and state checkpointing.

### Detailed Requirements:
1. **State Schema (`TypedDict`)**:
   - Define `HomaatriGraphState`: `messages`, `sender_phone`, `current_role`, `active_order_id`, `is_interrupted`, `checkpoint_data`.
2. **Graph Node Definitions**:
   - `MasterNode`, `CustomerNode`, `ChefNode`, `DriverNode`.
3. **Conditional Routing Edges**:
   - Write edge functions evaluating subagent intent payloads to route control (`CustomerNode` $\rightarrow$ `MasterNode` $\rightarrow$ `ChefNode`).
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
   - Automatically transition orders from `PENDING_PAYMENT` $\rightarrow$ `CONFIRMED` upon payment verification.
   - Handle incremental top-up payments for mid-cooking dish additions.
