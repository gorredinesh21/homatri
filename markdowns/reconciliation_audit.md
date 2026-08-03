# Reconciliation Audit — Plan vs. Implementation

**Date:** 2026-08-03
**Branch:** `from-scratch`
**Method:** Three-way cross-check of (1) design docs = the *plan*, (2) `app/` = the *code*, (3) `git log -S` = the *history*. Source-of-truth stance: **case-by-case, no default** — every divergence carries evidence; the human rules on each.

**Root-cause headline:** `git log -S` proves the 40-tool spec (`sample_scripts/all_40_agent_tools.py`) was **never implemented and then deleted — it was never built.** No commit anywhere adds the missing tool names into `app/`. The design docs describe a system that was authored on paper; `app/` is a separate, simplified interpretation that was never reconciled against the contract. So the audit is a **capability gap analysis**, not a recovery of lost code.

Verdict legend: `☐` = pending human ruling. Severity: 🔴 critical · 🟠 significant · 🟡 minor · 🟢 acceptable/no-action.

---

## 0. Cross-cutting systemic findings (span multiple layers)

| # | Finding | Evidence | Severity | Verdict |
|---|---|---|---|---|
| X1 | **HITL is broken end-to-end.** Tools create `system_hitl_sessions` rows, but the graph compiles with **no checkpointer** and has **no `interrupt()`/resume**, so no HITL conversation can ever pause and resume. Custom-dish, location-pin, and gate-issue flows are non-functional as a loop. | `graph.py:70` bare `compile()`; no `interrupt()` in `agents/*`; tools at `chef_tools.py:791`, `driver_tools.py:341` create HITL with no consumer | 🔴 | ☐ |
| X2 | **`master_tools.py` queries columns that don't exist.** `get_master_kitchen_availability_summary` sums `ChefDailyInventory.allocated_quantity` / `remaining_quantity`; neither is on the model. Live `AttributeError`. | `master_tools.py:176-177` vs `chef.py:80-91` | 🔴 | ☐ |
| X3 | **`generate_id` used but not imported in `master_tools.py`** (in payment webhook path). Live `NameError` (to re-verify). | earlier read of `master_tools.py` | 🟠 | ☐ |
| X4 | **Custom-dish / dietary flow is a dead-end.** Chef `respond_to_custom_request` answers a `CUSTOM_DISH_REQUEST` interrupt that **nothing creates** (only a test seeds it); escalation tool uses a different string `DIETARY_APPROVAL`. Half-built + inconsistent. | `chef_tools.py:791`; `master_tools.py:490,527`; grep: `CUSTOM_DISH_REQUEST` only in tests | 🟠 | ☐ |
| X5 | **Design docs are internally contradictory** (two "21 executor" specs; "24 vs 25" table count; stale `table_structure.md`). The plan itself is not a single coherent source. | see L2/L3 | 🟡 | ☐ |

---

## 1. Tools layer — 40 planned → 34 built (capability gaps)

Names drifted (renamed), so mapping is by *capability*, not string.

### 🔴 Genuinely missing capabilities
| Capability (planned tool) | Status in running system | Verdict |
|---|---|---|
| Customer live order status / driver ETA (`get_active_order_status`) | **Absent** — no tool anywhere lets a customer ask "where's my order?" | ☐ |
| Customer initiates custom-dish/dietary request (`relay_dietary_request_to_chef`) | **Absent** — see X4; chef side is orphaned | ☐ |

### 🟠 Degraded by fat-tool consolidation (judge on merit)
| Fat tool | Collapses | Capabilities LOST | Verdict |
|---|---|---|---|
| `confirm_stop_arrival_and_delivery` (`driver_tools.py:511`) | mark_driver_reached_stop, mark_orders_picked_up, mark_gate_delivery_completed, dispatch_next_leg_navigation_link | (a) two-phase *arrived vs delivered* — jumps straight to `COMPLETED`, no intermediate reached state; (b) structured `left_with_security` flag (only free-text notes) | ☐ |
| `report_delivery_delay_or_gate_issue` (`driver_tools.py:341`) | report_unlocatable_address_hitl, report_vehicle_delay_alert, relay_traffic_delay_alert | (a) route-wide ETA recalculation across affected stops (only single-customer alert); (b) unlocatable-address → request GPS location pin (`AWAIT_LOCATION_PIN`) — sends generic "please reply" instead | ☐ |

### 🟢 Folded but acceptable (not gaps)
| Planned tool | Where it lives now | Verdict |
|---|---|---|
| `validate_meal_cutoff_clock` | inline in `initialize_customer_order_tool` (`customer_tools.py:590-602`) — works; hardcoded/duplicated | ☐ |
| `update_customer_location_pin` | folded into `register_customer_profile` — **human-directed** | ☐ |
| `delegate_cross_domain_write` | superseded by executor architecture (arguably better) | ☐ |
| `log_system_audit_event` | exists as `execute_system_audit_log` executor, called internally | ☐ |
| `check_daily_inventory_status`, `check_driver_arrival_status`, `get_assigned_driver_info` | covered by `get_chef_daily_batch_checklist` / `get_chef_menu` / `get_assigned_driver_eta` | ☐ |

### ➕ 9 tools ADDED that were never in the 40-plan
`get_chef_menu`, `get_chef_earnings_summary`, `get_master_kitchen_availability_summary`, `get_master_order_pipeline_summary`, `trigger_hitl_escalation`, `escalate_delayed_batch_prep`, `request_cut_off_extension`, `register_driver_profile`, `update_driver_duty_status`. → **Verdict per tool:** ☐

---

## 2. Executors layer — 21 planned → 21 built (HEALTHIEST layer)

All 21 present, correctly named, in the right files. DW1 `VALID_TRANSITIONS` (`customer.py:272-282`) and DW2→DW1 cascade (`customer.py:351-359`) both implemented as designed.

| # | Finding | Evidence | Severity | Verdict |
|---|---|---|---|---|
| E1 | Single-owner invariant violated: `execute_add_item_to_order` writes `customer_orders` subtotals (not via DW1) | `customer.py:189-190` | 🟠 | ✅ **doc-only — NOT a bug.** Add-item legitimately owns the money columns; the real invariant is "status changes only via DW1," which IS honored. No code change. |
| E2 | Single-owner violated: both driver trip executors mutate `driver_profiles.current_assigned_route_id` | `driver.py:101,166` | 🟡 | ☐ |
| E3 | Single-owner violated: cutoff executor creates `system_delivery_stops` though plan gives that table to `execute_stop_status_update` | `master.py:121-137` | 🟡 | ☐ |
| E4 | `execute_cutoff_batch_lock_and_routes_creation` **does not actually lock the meal window** despite name/docstring — never touches `system_meal_windows` (window was locked by a raw write in the tool at `master_tools.py:415` that skipped `locked_at`) | `master.py:79-158` | 🟠 | ✅ **FIXED** — executor now sets `status=LOCKED_PROCESSING` + `locked_at`. Tool's raw write to be dropped in tool rewrite. (import-verified; DB test pending on Postgres laptop) |
| E5 | Cross-domain writes correctly routed through DW1 (Guard 3 honored) — no violation | `master.py:18,149-155` | 🟢 | — |
| E6 | Two conflicting "21 executor" specs; guards-doc variant has Admin executors + `execute_stop_eta_recalculation` never built | `data_integrity...md:123-154` | 🟡 | ☐ |

---

## 3. Models / columns layer — 25 planned → 25 built (clean table parity)

Every finalized-plan table has a model; no orphan models. Drift is at column/enum level only.

| # | Finding | Evidence | Severity | Verdict |
|---|---|---|---|---|
| M1 | `ChefOrderReadiness` **missing `driver_notified`** column promised in plan (was only a throwaway local var in `chef_tools.py:650,683`, never persisted) | `chef.py:94-108` vs `chef_tables.md:124` | 🟠 | ✅ **FIXED** — `driver_notified: bool` column added (default `True`). Column registered; DB test pending on Postgres laptop. |
| M2 | `ChefDailyInventory` has no `allocated_quantity`/`remaining_quantity` — **correct** per plan+model; but see X2 (tool queries them) | `chef.py:80-91` | 🔴 (via X2) | ☐ |
| M3 | `OrderStatus.DRAFT_CART` exists in code enum but not in plan's order lifecycle | `enums.py:24` vs `customer_tables.md:71` | 🟡 | ☐ |
| M4 | `driver_profiles.current_assigned_route_id` is soft-ref (no FK) vs plan's FK annotation — documented deliberate deviation | `driver.py:25-27` | 🟢 | ☐ |
| M5 | Plan's "24 tables" header miscounts its own 25-item breakdown; `table_structure.md` (18-table) is fully stale | `docs/history.txt:697` | 🟡 (doc-only) | ☐ |

---

## 4. Runtime layer — M3 "locked" design largely NOT implemented (MOST DIVERGED)

| # | Locked design | Runtime code | Severity | Verdict |
|---|---|---|---|---|
| R1 | `AsyncPostgresSaver` checkpointer, `thread_id=phone` (Locked #4) | bare `builder.compile()` — none | 🔴 | ☐ |
| R2 | Option B custom loop + per-node tool isolation (Locked #2) | Option A prebuilt `ToolNode` with ALL tools; no isolation at execution | 🟠 | ☐ |
| R3 | DB-backed Context Assembler, both-sides timestamped (Locked #6) | `state["messages"][-12:]` in-memory slice | 🟠 | ☐ |
| R4 | 7-field state incl. `target_node`, `event_payload`, `current_input` | 6 fields; those 3 dropped → node-authored hand-off mechanism gone | 🟠 | ☐ |
| R5 | Entry flow: normalize→dedup→fresh-vs-resume→resolve-identity + `interrupt()` HITL + 15-min TTL worker | none; `START → master_router_node` role lookup only | 🔴 (ties to X1) | ☐ |
| R6 | System prompt "never invent prices/menus" | hardcoded fallback replies incl. invented Ghansoli menu on LLM exception | 🟠 | ☐ |
| R7 | Router: nodes write `target_node`, central `route_by_target` | inverted — per-cycle DB role lookup via `route_by_role`; `tools→master_router_node` loop | 🟡 | ☐ |

---

## Priority summary (before verdicts)

- **🔴 Blockers for a working system:** X1 (HITL loop), X2 (live crash in master kitchen summary), R1/R5 (no checkpointer/interrupt).
- **🟠 Real feature/quality gaps:** missing customer order-status tool, dead-end custom-dish flow (X4), degraded driver fat tools, E1/E4 executor issues, R2/R3/R4/R6 runtime.
- **🟡 Doc hygiene:** conflicting specs (X5), DRAFT_CART, stale `table_structure.md`.
- **🟢 Healthiest:** executors (21/21) and table parity (25/25).

**Next step:** walk this ledger row-by-row to assign verdicts (keep / fix / restore / doc-only), then fix strictly one-at-a-time in severity order.
