# 🕸️ Master Specification: LangGraph State Machine & Graph (Milestone 3)

This document specifies the complete multi-agent LangGraph runtime for Homaatri — the "nervous system" that wires the 40 tools (M2) and 24 tables (M1) into a live, durable, multi-agent conversation.

**Mental model:** typical `LLM + @tool functions` *inside* each node; a durable **StateGraph** *around* them for agent hand-offs and pause/resume. We use LangGraph for its production-grade core (state graph, checkpointer, HITL, routing); business logic (tools/executors/guards/DB) stays framework-agnostic so version churn only touches the thin wiring layer.

---

## 🧩 The 5 Pieces
1. **State schema** — the shared clipboard (`TypedDict`) that flows through the graph.
2. **Nodes** — 3 specialist agent tool-loops + 1 Master orchestrator.
3. **Router (edges)** — decoupled agent-to-agent hand-offs.
4. **Checkpointer** — Postgres durability + HITL pause/resume + per-user isolation.
5. **Entry flow** — how inbound messages / system events enter and route.

---

## 📋 Piece 1: State Schema (`HomaatriGraphState`)

The lean clipboard. Carries **conversation + control-flow + identity + hand-off intent ONLY**. All domain data (orders, menus, live status) is read **fresh from Postgres inside each node** every turn (single source of truth → never goes stale).

```python
from typing import Annotated, Optional, TypedDict
from langgraph.graph.message import add_messages

class HomaatriGraphState(TypedDict):
    messages:        Annotated[list, add_messages]  # 1. conversation window (APPENDS via reducer)
    sender_phone:    str                            # 2. who's talking + the thread_id key
    current_role:    str                            # 3. CUSTOMER / CHEF / DRIVER / MASTER / UNKNOWN
    target_node:     str                            # 4. routing signal: which node runs next
    event_payload:   dict                           # 5. clean cross-agent hand-off intent
    active_order_id: Optional[str]                  # 6. order currently in focus
    current_input:   dict                           # 7. parsed inbound {type, text, latitude, longitude, button_id}
```

- `messages` uses the built-in `add_messages` **reducer** → nodes append, never overwrite.
- `current_input` is a separate structured field (WhatsApp sends LOCATION pins & BUTTON taps, not just TEXT).

---

## 🧠 Piece 2: Nodes

A node = a Python function: **receives the clipboard → returns only the fields to update.** LangGraph merges the result.

### Inner tool-loop implementation: **Option B (custom loop) — LOCKED**
We write the ~12-line ReAct loop ourselves (NOT LangGraph's `create_react_agent`), because our nodes need precise control: Context Assembler, Guard-2 pre-condition asserts, custom `target_node`/`event_payload` routing, and `interrupt()` at exact HITL points. Option B uses only stable low-level primitives (`bind_tools`, `.invoke`, `.tool_calls`, `ToolMessage`) → lower framework lock-in. Latency is dominated by LLM calls; loop mechanics are negligible (B is marginally leaner on checkpoint writes).

### Anatomy of a specialist node
```python
def customer_node(state):
    # STEP 1 — Context Assembler (see contract below)
    msgs = assemble_context(state["sender_phone"], state["current_input"])
    # STEP 2 — bind ONLY this agent's tools (isolation)
    llm_with_tools = llm.bind_tools(CUSTOMER_TOOLS)   # 11 customer tools only
    # STEP 3+4 — ReAct loop (bounded by recursion_limit)
    for _ in range(10):
        ai = llm_with_tools.invoke(msgs)
        msgs.append(ai)
        if not ai.tool_calls:
            break
        for call in ai.tool_calls:
            output = TOOL_MAP[call["name"]].invoke(call["args"])  # Guard-2 asserts + Guard-1 txn live inside the tool
            msgs.append(ToolMessage(output, tool_call_id=call["id"]))
    # STEP 5 — update clipboard
    return {"messages": [ai], "target_node": decide_route(ai, state)}
```

### Tool isolation (per node)
| Node | Binds | Role |
|---|---|---|
| `customer_node` | 11 customer tools | onboard, discover, order, pay, track, review |
| `chef_node` | 9 chef tools | inventory, batch checklist, mark packed, counter-offers |
| `driver_node` | 8 driver tools | route, next-leg links, stop handshakes, exceptions |
| `master_node` | 12 master tools | **orchestrator/delegator/relay** — not a chatty specialist; routes hand-offs, runs delegated executors, manages HITL, handles system events |

### 🕐 Context Assembler contract (applies to EVERY LLM call)
**Never send the bare current message.** Before each `llm.invoke`:
1. From `conversation_messages` for `sender_phone`, fetch the last **3-4 `INBOUND` (user)** + last **3-4 `OUTBOUND` (agent)** messages (~6-8 total — both sides guaranteed).
2. **Merge + sort chronologically** by `created_at`.
3. **Format each line with its timestamp + speaker** (speaker derived from `direction`/`source`, e.g. `Customer` / `Agent` / `System notice`).
4. **Append the current message** last.
5. Send that window (under the system prompt) to the LLM.

Example passed to the LLM:
```
[2026-07-31 12:05:30] Customer: Hi
[2026-07-31 12:05:31] Agent: Welcome! Want to see kitchens near you for lunch?
[2026-07-31 12:05:58] Customer: whats on ramesh kitchen menu
[2026-07-31 12:06:00] Agent: Ramesh Kitchen — Paneer Thali ₹180, Veg Thali ₹150. Add any?
[2026-07-31 12:06:20] Customer: yes            ← current message
```
Timestamps let the LLM reason about time (cutoff proximity, "asked an hour ago"). Terse replies like "yes"/"okay" resolve against the window. **Escalation guardrail:** default 3-4 each; the LLM may pull more (hard cap ~10 each) if context is insufficient.

---

## 🔀 Piece 3: Router (Edges)

**Pattern: nodes decide, the router dispatches.** No node calls another node directly (zero circular imports). A node writes `target_node` on the clipboard; one central conditional edge reads it.

```python
from langgraph.graph import END
VALID_NODES = {"customer_node", "chef_node", "driver_node", "master_node"}

def route_by_target(state) -> str:
    target = state.get("target_node", END)
    return target if target in VALID_NODES else END

for node in VALID_NODES:
    graph.add_conditional_edges(node, route_by_target)
```

- **Static edge** = always A→B; **conditional edge** = function returns next node name; **`END`** = turn done, state checkpointed.
- **Safety net:** graph invoked with `recursion_limit = 10` → hard-stops agent ping-pong.

Topology: `ENTRY → {customer|chef|driver|master}_node → route_by_target → {…node|END}`.

---

## 💾 Piece 4: Checkpointer

LangGraph's auto-save: after every node, the clipboard is written to Postgres keyed by `thread_id`. Gives: durability (survive restarts), HITL pause/resume, per-user isolation, cross-turn memory.

- **Backend:** `AsyncPostgresSaver` (matches Postgres + async FastAPI). `MemorySaver` for unit tests only. It manages its own tables (`checkpoints`, `checkpoint_writes`, `checkpoint_blobs`), separate from the 24 business tables.
- **`thread_id = normalized phone`** → one persistent conversation thread per user; concurrent chats never mix.

### ⭐ 3-way state ownership (the key decision)
| Layer | Owns |
|---|---|
| **LangGraph checkpointer** | *live* control-flow for pause/resume (`target_node`, `event_payload`, `active_order_id`, `current_role`, current-turn messages). Kept lean — message context is rebuilt per turn from `conversation_messages`, not accumulated here. |
| **`conversation_messages`** | the **authoritative** complete transcript + the source the Context Assembler reads. |
| **`system_hitl_sessions`** | the **business** HITL ledger: what we wait on, from whom, payload, **`expires_at` (15-min TTL)**, `default_on_expiry`. |

### ⏳ 15-minute TTL + expiry worker
`interrupt()` can hang forever if a human ghosts us. So on each pause we stamp `system_hitl_sessions.expires_at = now()+15m` with a `default_on_expiry` fallback. A **background worker** polls `WHERE status='WAITING' AND expires_at < now()`, **resumes those graphs with the safe default** (e.g. `chef_approved=false`), and marks them `EXPIRED` — so every conversation always reaches a resolution. (This is business logic the generic checkpointer can't do → why `system_hitl_sessions` exists separately.)

---

## 🚪 Piece 5: Entry Flow

Three front doors funnel into the same graph.

### A. WhatsApp inbound (main path)
1. **Normalize phone** → canonical 10-digit.
2. **Dedup + parse** → skip seen `wa_message_id`; build `current_input`; log inbound to `conversation_messages`.
3. **Fresh vs. resume?** — check if the thread has a pending interrupt (`graph.get_state(config)` / a `WAITING` `system_hitl_sessions` row). If yes → **resume** the paused graph feeding in this reply (e.g. location pin during onboarding, chef "YES", customer paid). If no → **fresh** turn.
4. **Resolve identity** (fresh only) in a small `entry_node`:
   ```
   phone in chef_profiles?      → CHEF     → chef_node
   elif in driver_profiles?     → DRIVER   → driver_node
   elif in customer_profiles?   → CUSTOMER → customer_node
   else (unknown)               → UNKNOWN  → customer_node (onboarding)
   ```
   (Chefs & drivers are Admin-onboarded so they exist pre-message; unknown phone = new customer.)
5. **Run graph** under `config={"configurable":{"thread_id": phone}}` → reply dispatched outbound + logged.

### B. Cutoff cron (12 PM / 7 PM) → enters at `master_node` → batch-lock + route optimization.
### C. Payment webhook (Razorpay) → enters at `master_node` → verify + dedup → resumes the customer's `PAYMENT_AWAIT_PROVIDER` interrupt.

---

## ✅ Locked Decisions (M3)
1. State = 7 lean fields; domain data read fresh from Postgres (never on the clipboard).
2. Inner tool-loop = **Option B (custom loop)**; LangGraph kept at the edges; version pinned.
3. Router = single `route_by_target` reading `target_node`; decoupled; `recursion_limit=10`.
4. Checkpointer = `AsyncPostgresSaver`; `thread_id=phone`; 3-way state split; 15-min TTL via background worker.
5. Entry = normalize → dedup → fresh-vs-resume → resolve role → run; + cron + payment-webhook doors.
6. **Context Assembler**: every LLM call gets a timestamped, both-sides, last-3-4-each window from `conversation_messages` + current message appended.
