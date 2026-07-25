# 🧠 Homaatri — AI Agent Architecture Specification

This document details the multi-agent system architecture, database access protocols, communication topologies, and operational lifecycle rules for the **Homaatri** platform.

---

## 🏛️ 1. Architecture Overview & Core Topology

Homaatri uses a **Hub-and-Spoke Mediator Topology** paired with **Domain-Isolated Data Ownership**. The system consists of four primary AI agents:

```
                      ┌──────────────────────────────┐
                      │                              │
                      │     MASTER / MANAGER AGENT   │
                      │  (Central Orchestrator / DB) │
                      │                              │
                      └──────────────┬───────────────┘
                                     │
         ┌───────────────────────────┼───────────────────────────┐
         │ (Mediated Relay)          │ (Mediated Relay)          │ (Mediated Relay)
         ▼                           ▼                           ▼
┌──────────────────┐       ┌──────────────────┐       ┌──────────────────┐
│  CUSTOMER AGENT  │       │    CHEF AGENT    │       │   DRIVER AGENT   │
│ (Order & Care)   │       │  (Kitchen Ops)   │       │  (Route & Drop)  │
└──────────────────┘       └──────────────────┘       └──────────────────┘
```

### Key Principles:
1. **No Direct Subagent-to-Subagent Communication**: Subagents (Customer, Chef, Driver) cannot talk directly to each other. All inter-role messages must be routed through the Master Agent.
2. **Master Agent Oversight**: The Master Agent can communicate directly with all three subagents and orchestrates cross-role handshakes.
3. **Decoupled Responsibilities**: Each subagent maintains its own prompt context, tool belt, and domain focus.

---

## 🔐 2. Database Permissions & Data Access Protocol

To ensure strict data security and prevent multi-agent race conditions or data corruption, database access follows a strict permission model:

```
┌─────────────────┬─────────────────────────────┬─────────────────────────────────────────┐
│ Agent           │ Read Access                 │ Write Access                            │
├─────────────────┼─────────────────────────────┼─────────────────────────────────────────┤
│ Customer Agent  │ Global (All Tables)         │ Scoped: `customer_*` tables only        │
│ Chef Agent      │ Global (All Tables)         │ Scoped: `chef_*` tables only            │
│ Driver Agent    │ Global (All Tables)         │ Scoped: `driver_*` tables only          │
│ Master Agent    │ Global (All Tables)         │ Global: All Database Tables             │
└─────────────────┴─────────────────────────────┴─────────────────────────────────────────┘
```

### Read vs. Write Rules:
- **Global Read Access**: All 4 agents have unrestricted read access across all database tables (both same-domain and cross-domain). This enables zero-latency information lookups (e.g. Customer Agent reading kitchen status directly from DB without asking Master Agent).
- **Scoped Domain Write Access**: Subagents can **only** write to their own domain-specific tables.

---

## 🔄 3. Cross-Domain Write Delegation Protocol

When an action requires modifying data outside a subagent's domain, the following routing rules apply:

```
               [Case 1: Subagent-Requested Cross-Domain Write]

  Customer Agent ─────────── Request Write ───────────► Master Agent
  (wants to update                                          │
   Chef/Driver data)                                        │ Delegate Write
                                                            ▼
                                                       Chef / Driver Agent
                                                            │
                                                            │ Perform Write
                                                            ▼
                                                     Target DB Table


                 [Case 2: Master-Initiated System Write]

  System Event / ─────────────────────────────► Master Agent
  Cutoff Timer                                      │
                                                    │ Direct Write
                                                    ▼
                                               Target DB Table
```

### Execution Rules:
1. **Subagent-Requested Cross-Domain Writes**:
   - If a subagent needs to alter data outside its domain (e.g. Customer Agent requests a mid-prep order modification that affects Chef or Driver tables):
   - Subagent sends the write request to the **Master Agent**.
   - Master Agent **delegates the request to the target domain subagent**.
   - Target domain subagent validates and executes the write in its own domain tables.
2. **Master-Initiated System Writes**:
   - If a write is triggered by system-level orchestration (e.g., 12:00 PM / 7:00 PM cutoff timers, batch status updates), the Master Agent executes the write directly using its global write authority.

---

## ⏰ 4. Meal Windows & Cutoff Policy Engine

Homaatri operates on a strict batch-scheduled model rather than impulse delivery.

- **Services Offered**: Lunch and Dinner.
- **Lunch Cutoff Gate**:
  - Orders MUST be placed before **12:00 PM**.
  - Any lunch order attempt after 12:00 PM is automatically rejected by the Policy Engine.
- **Dinner Cutoff Gate**:
  - Orders MUST be placed before **7:00 PM**.
  - Any dinner order attempt after 7:00 PM is automatically rejected by the Policy Engine.

---

## ⚙️ 5. Operational Workflow Sequences

### A. Order Ingestion (Customer $\rightarrow$ Master $\rightarrow$ DB)
1. Customer messages *"Hi"* on WhatsApp.
2. Customer Agent processes the conversation, displays menu choices, checks current time against meal cutoffs, and calculates subtotal.
3. Customer Agent writes confirmed order data directly to `customer_orders` table.

### B. Cutoff Trigger & Batch Chef Dispatch (Master $\rightarrow$ Chefs)
1. At cutoff time (12:00 PM for Lunch / 7:00 PM for Dinner), Master Agent triggers batch aggregation.
2. Master Agent queries all confirmed orders for the meal window and generates consolidated checklists per chef.
3. Master Agent dispatches individual checklists to each Chef Agent.

### C. Order Ready Signal & Driver Dispatch (Chef $\rightarrow$ Master $\rightarrow$ Driver)
1. When food is cooked and packed, Chef sends signal: *"Order #104 is ready"*.
2. Chef Agent writes ready status to `chef_kitchen_status` table.
3. Master Agent receives signal and notifies Driver Agent.
4. Driver Agent generates optimized TSP route pin for drop-off.

### D. AI Customer Care & Live Tracking (Customer $\leftrightarrow$ AI)
1. Customer asks on WhatsApp: *"Where is my driver?"*.
2. Customer Agent uses Global Read Access to read live driver location and route progression directly from `driver_locations` table.
3. Customer Agent responds immediately to customer with live status.

---

## 📚 6. Theoretical Foundation & References

This architecture synthesizes established patterns from industry literature:

- **Microsoft Azure AI Agent Design Patterns**: Combines *Sequential Pipelines* (order processing), *Concurrent Analysis* (route solvers), and *Handoff Delegation* (Master $\leftrightarrow$ Subagent routing).
- **LangChain Architecture Framework**: Utilizes *Subagent Context Isolation* to prevent token bloat while supporting *Stateful Handoffs*.
- **Nicole Koenigstein (O'Reilly Radar)**: Enforces *System-Level Architectural Boundaries* over prompt engineering (*The Prompting Fallacy*).
