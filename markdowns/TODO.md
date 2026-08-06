# Homaatri — TODO / status

**Branch:** `homatri_1.0` · **Updated:** 2026-08-04

## 🖥️ Moving to a personal laptop (mock → real) — see [RUN_ON_PERSONAL_LAPTOP.md](RUN_ON_PERSONAL_LAPTOP.md)
On the office laptop every external dependency is a mock/stand-in. When we run on a personal laptop
these all need to switch to real services. Full checklist (files, env vars, gotchas) in that doc; summary:
- **LLM** 🔴 Bedrock/Kimi (in `dev_server.py`) → **Gemini** (`app/agents/llm.py` exists but is unused today — real code change, not just a key).
- **Database** 🟢 SQLite (`poc.db`) → **PostgreSQL** (config default is already Postgres; flip `DATABASE_URL`).
- **Payments** 🟡 Razorpay `mock_mode` → real keys + `RAZORPAY_MOCK_MODE=False` + a public webhook URL wired to `process_payment_webhook`.
- **Maps** 🟡 nearest-neighbour mock → real Google Routes API (set `GOOGLE_MAPS_API_KEY`).
- **WhatsApp** 🟡 tester widgets → Meta Cloud API (only for a true deployment).

## ✅ Done — Customer Flows 1–3 (built + tested, 27 tests green on SQLite)
- **Flow 1 Onboarding:** `get_customer_profile`, `register_customer` (+ `send_and_await_reply` pause / `finish_registration` resume for the location pin).
- **Flow 2 Discovery:** `find_nearby_kitchens` (window by the clock, Haversine sort, top 5).
- **Flow 3 Browse & Order:** `view_chef_menu`, `create_order`, `add_item_to_order`, `view_cart`.
- **Name-based resolution:** tools take kitchen/dish **names** (`_resolve_chef` / `_resolve_dish`), not IDs — the LLM no longer fabricates IDs. Guards: `NOT_FOUND`, `AMBIGUOUS`, `ORDER_EXISTS`, `NO_ACTIVE_ORDER`, `ALREADY_REGISTERED`.
- **Dev harness** (`dev_server.py`): multi-widget WhatsApp tester on the real `/webhook` path; LLM = AWS Bedrock **`moonshotai.kimi-k2.5`** (~4s/turn); `dev_reset.py` seeds 4 chefs + `delivery_fee=20`, no customer.

## ▶️ Next — Flow 4: Payment
- `request_payment` (Customer agent → **Master** owns the gateway) → payment link → webhook → order `PENDING_PAYMENT` → `CONFIRMED`.
- Executors already present: `execute_payment_record_creation` (DW: creates PENDING payment), `execute_payment_status_update` (DW2: PAID → cascades order to CONFIRMED via DW1).
- Open design questions to settle first: real vs simulated gateway for dev; how the Customer agent hands off to Master; what the customer sees while awaiting payment.

## ⚠️ REMINDER — seed `system_settings` in a real migration
Currently read from `system_settings` (JSON `value`) with code fallbacks; cutoffs are still constants:
- `delivery_fee` → `{"amount": 20}` (create_order reads this; falls back to config ₹30).
- `cutoff_lunch` 11:30, `cutoff_dinner` 18:30 — hardcoded in `app/tools/common.py`; move here.
- `timezone` → `Asia/Kolkata`.

## Later / deferred runtime
- DB-backed Context Assembler over `conversation_messages` (harness uses in-memory last-4 window).
- LangGraph checkpointer + `interrupt()`/resume (harness uses the in-memory pause store).
- Chef spoke, Driver spoke, Master cutoff/route engine.

## Standing rules (do not forget)
- **Discuss-then-build**, one tool/piece at a time.
- Tools are **pure deterministic code — guard-then-guide** (if/else guards return fixed templates telling the agent the next tool). No LLM call inside a tool.
- **Read any table; write only your own; cross-domain writes go Master → owner executor.**
- Foundation (25 tables + 21 executors) is **frozen** — don't edit without an explicit decision.
- Harness LLM is **AWS Bedrock / Kimi K2.5** (office laptop can't reach GCP). Design docs mention Vertex/Gemini — treat that as aspirational, not current.
