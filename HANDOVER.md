# 🤝 Homaatri — Handover & Resume Document

_Last updated: 2026-07-30 · Branch: `from-scratch`_

This document captures **what's done, what's decided, and exactly where to resume**. Read this first when picking the project back up.

---

## 1. What Homaatri is

A **WhatsApp-native, batch-scheduled home-food delivery platform**. Design philosophy: **"the AI agents are the runtime; the backend is their tool belt."** Three real actors chat only via WhatsApp — **Customers**, home-cook **Chefs**, delivery **Riders** — orchestrated by a **4-agent AI system** (Master + Customer/Chef/Driver), plus an **Admin** back-office.

**Business rules:** Lunch & Dinner only. Hard cutoffs — lunch before **12:00 PM**, dinner before **7:00 PM**. At cutoff → batch → chef checklists + **one** GCP route-optimization call. Deliveries to **apartment gates/security** (not doorstep); orders sharing a gate consolidate into one stop. Financials are **system-authored** (LLM never invents prices/URLs). Engine: **Gemini 3.6 Flash** on GCP Vertex AI (project `homatri-503308`).

---

## 2. Branch & workspace state

- Working on branch **`from-scratch`** — a deliberate clean rebuild. Old full implementation is preserved in `main` / `aws-bedrock` / `gcp-gemini` / `gcp-poc-upgrade`.
- On `from-scratch` there is **no app code yet** — only design docs, reference LLM scripts (`llm.py`, `llm_direct.py`), the 40-tool blueprint (`all_40_agent_tools.py`), the LangGraph demo (`demo_langgraph_agents.py`), and the `markdowns/` design set.
- ⚠️ The `app/`, `tests/` folders + `.env`, `poc.db` in the working tree are **orphaned artifacts** from a previous branch (only `.pyc` bytecode). They are **untracked and not part of this branch** — ignore or clean up.

---

## 3. Roadmap status (5 Milestones)

| # | Milestone | Status |
|---|---|---|
| 2 | Agent Tools Design & Pydantic Mapping (40 tools) | ✅ **Done** |
| 1 | Database schema (18+ tables) | 🔄 **In progress** — see §4 |
| 3 | LangGraph State Machine & Graph | ⬜ Pending |
| 4 | WhatsApp Business API Webhook Gateway | ⬜ Pending |
| 5 | Payment Gateway Webhook & Financial Flow | ⬜ Pending |

---

## 4. Milestone 1 — where we are (THE ACTIVE WORK)

We are designing the database **entity by entity**, using a **bottom-up query-inventory method**: for each agent, walk every tool, list the queries it runs, and let the tables emerge; then merge/dedupe.

### ✅ Completed (decided & documented)
For **all 6 entities**, we have decided: the **set of tables**, their **ownership**, **primary keys**, **indexes**, and all **cross-domain / handoff / delegation protocols**. Documented in `markdowns/*_tables.md` and appended to each agent spec (`§3`) and `history.txt` (Sections 22–26).

Entities & their tables:
- **Chef** (`chef_*`): `chef_profiles`, `chef_menu_items`, `chef_daily_inventory`, `chef_order_readiness`
- **Customer** (`customer_*`): `customer_profiles`, `customer_orders`, `customer_order_items`, `customer_payments`, `customer_reviews`
- **Rider/Driver** (`driver_*`): `driver_profiles`, `driver_trip_status` _(driver_locations DROPPED — no GPS)_
- **Master/System** (`system_*`): `system_meal_windows`, `system_settings`, `system_delivery_routes`, `system_delivery_stops`, `system_delivery_stop_orders`, `system_agent_logs`, `system_outbound_queue`, `system_hitl_sessions`, `system_payment_webhook_events`, `system_route_optimization_runs` _(observability)_
- **Admin** (`admin_*`): `admin_users`, `admin_activity_log`, `admin_ai_queries` _(future)_
- **Runtime (shared):** `conversation_messages` — unified chat log (replaces per-domain `*_chat_history` + `system_inbound_messages`)

### 🔄 NOT finished — RESUME HERE
**Exact table COLUMNS are NOT finalized.** The column lists in the `*_tables.md` files are **provisional/candidate**, not locked. We started finalizing columns **entity → table by table** and paused.
- **`chef_profiles`** columns were tentatively drafted & lightly edited (in chat, not yet saved to file).
- **Next table to do:** `chef_menu_items`, then the rest of Chef, then Customer → Rider → Master/System → Admin.
- **Column philosophy agreed:** data volume is low, so **be generous** — include useful "nice-to-have / future" columns; extra plain columns don't hurt reads. BUT keep **cross-domain aggregates derived** (e.g. `units_sold`, `avg_rating`) — their cost is write-consistency, not read.

### ⬜ After columns
Merge/dedupe all passes into the **final schema DDL** (`CREATE TYPE` enums + `CREATE TABLE`s). **Open decision:** target **PostgreSQL** (matches all docs: real ENUMs, TIMESTAMPTZ, pgvector-ready) vs **SQLite** first.

---

## 5. Locked design decisions (apply everywhere)

1. **Phone = natural key.** Tables key on normalized phone (`chef_phone`/`customer_phone`/`driver_phone`); transactional rows (orders/items/payments/routes) keep `VARCHAR(36)` surrogate ids. Admin is the exception (not phone-based → `admin_id` + unique email).
2. **Phone normalization (at webhook ingress):** strip spaces + leading `+` → 12 digits starting `91` take last 10 · 10 digits as-is · else invalid. (Indian numbers only for now.)
3. **Write-invariant:** a subagent tool WRITES only its own domain tables; READS are global. Every cross-domain write is a **HANDOFF to Master**.
4. **Master delegates-only:** Master has NO direct global-write tool. It writes **only `system_*` directly**, and **delegates** subagent-domain writes to that domain's deterministic executor (avoids LLM tool confusion). Master handoff has two kinds: target = System → Master writes direct; target = subagent → Master delegates.
5. **Delegated-write executors** (where cross-domain writes land): Customer **DW1** `execute_order_status_transition` (BATCHED/PACKED/PICKED_UP/DELIVERED/CONFIRMED/CANCELLED), **DW2** `execute_payment_status_update` (PAID/FAILED/REFUNDED).
6. **Derived, not stored** (avoid cross-domain writes): `units_sold` = SUM of `customer_order_items`; chef/driver `avg_rating` = from `customer_reviews`.
7. **Immutable snapshots** on orders/items: `kitchen_name`, `dish_name`, `unit_price`, `service_date` captured at order time (correct receipts + no cross-domain joins).
8. **Payment protocol:** Customer computes final amount → Master owns the gateway (mints UPI link) → **two LangGraph interrupts**: Master waits on the provider webhook (`PAYMENT_AWAIT_PROVIDER`), Customer waits on Master's approval (`PAYMENT_AWAIT_MASTER_APPROVAL`).
9. **Onboarding (Customer):** 2-phase HITL `register` (write details → `interrupt()` for location pin → save coords). Tool 3 folded in (11→10 customer tools).
10. **`conversation_messages`:** unified, INSERT-only, **written by the runtime** (not agents) — webhook logs inbound, dispatcher logs outbound (incl. Master notifications). Global read. Context Assembler fetches last 4-5 messages (LLM may escalate to 10, capped).
11. **`driver_locations` dropped** — no live GPS; rider progress = `driver_trip_status` (`current_stop_index`) ⋈ `system_delivery_stops`; this is the **live-tracking board** read by Customer & Chef (future map view).
12. **Access ladder (4 tiers):** Subagent (global read, scoped write, cross-domain via Master delegate) · Master (global read, own `system_*` write, delegate rest) · **Admin-human** (global read + **global write, audited**) · **Admin-AI** (global read, **NO write — strictly read-only NL→SQL**).
13. **HITL infrastructure:** `system_hitl_sessions` (interrupt checkpoints, 15-min TTL, expiry worker) — consumers: dietary counter-offer, unlocatable-address, cancellation, onboarding location, payment. Types in `hitl_interrupt_type_enum`.
14. **4 golden defensive coding rules** (from `agent_coding_rules_and_observability.md`): return_direct/END terminals · hard Python pre-condition asserts before writes · strict Pydantic tool schemas · clean TypedDict state hygiene. Plus `recursion_limit=10`, LangSmith tracing.

---

## 6. Enums decided (final names TBD in DDL)
`meal_window_enum` (LUNCH, DINNER) · `order_status_enum` (PENDING_PAYMENT, CONFIRMED, BATCHED, COOKING, PACKED, PICKED_UP, DELIVERED, CANCELLED) · `payment_status_enum` (PENDING, PAID, FAILED, REFUNDED) · `payment_type_enum` (INITIAL, TOPUP, REFUND) · `stop_type_enum` (PICKUP_KITCHEN, DROPOFF_GATE) · `readiness_status_enum` (PREPARING, PACKED_READY) · `trip_status_enum` (ASSIGNED, EN_ROUTE_PICKUP, AT_KITCHEN, EN_ROUTE_DELIVERY, AT_GATE, COMPLETED) · `window_status_enum` (OPEN, LOCKED_PROCESSING, COMPLETED) · `route_status_enum` (ASSIGNED, IN_PROGRESS, COMPLETED) · `stop_status_enum` (PENDING, ARRIVED, COMPLETED) · `log_severity_enum` (INFO, WARNING, CRITICAL) · `outbound_status_enum` (QUEUED, SENT, DELIVERED, READ, FAILED) · `hitl_status_enum` (WAITING, RESUMED, EXPIRED, RESOLVED) · `hitl_interrupt_type_enum` (DIETARY_APPROVAL, CANCELLATION_APPROVAL, UNLOCATABLE_ADDRESS, AWAIT_LOCATION_PIN, PAYMENT_AWAIT_MASTER_APPROVAL, PAYMENT_AWAIT_PROVIDER) · `admin_role_enum` (SUPER_ADMIN, OPS, SUPPORT) · plus `direction`/`actor_role`/`source`/`message_type` for `conversation_messages`.

---

## 7. File map

```
markdowns/
  ai agent architecture.md              # topology, read/write matrix, delegation
  table_structure.md                    # original 18-table inventory
  todo_architecture_roadmap.md          # 5 milestones
  action_dependencies_and_build_order.md# 4-tier build order
  backend_concurrency_and_state.md      # FastAPI/asyncio/thread_id isolation
  langgraph_hitl_relay.md               # HITL relay pattern
  agent_coding_rules_and_observability.md
  chef_agent.md / customer_agent.md / master_agent.md / delivery_driver_agent.md
                                        #   ^ each has "§3 Table Design & Query Access Map"
  chef_tables.md / customer_tables.md / driver_tables.md / master_tables.md / admin_tables.md
                                        #   ^ per-entity query pass + tables + keys (COLUMNS provisional)
history.txt                             # append-only ledger (Sections 1–26)
all_40_agent_tools.py                   # 40 LLM tool blueprint (stubs)
demo_langgraph_agents.py                # decoupled routing demo
llm.py / llm_direct.py                  # verified Gemini 3.6 Flash on Vertex AI
```

**Process rules:** all markdown docs live in `markdowns/`; every meaningful step gets a new numbered section in `history.txt`.

---

## 8. TL;DR — resume in one line
Milestone 1 tables/keys/indexes/access-model/protocols are **decided**; **resume by finalizing exact COLUMNS** entity→table (next up: `chef_menu_items`, Chef entity), then **merge into the final DDL** (decide Postgres vs SQLite).
