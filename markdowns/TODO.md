# Homaatri — TODO (next session)

**Branch:** `homatri_1.0` · **Date written:** 2026-08-04

## Where we are
Design phase is **complete**. `from-scratch` = full archive. `homatri_1.0` = clean slate (Foundation only: 25 tables + 21 executors + infra) + full design docs:
- [user_flows.md](user_flows.md) — 7 flows, locked principles, save/resume mechanism.
- [tool_specs.md](tool_specs.md) — **42 tools** fully specced (Customer 13, Chef 6, Driver 8, Master 13, Shared 2), domain → same/cross, with inputs/outputs/guards/reads/writes/executors.
- [tools_inventory.md](tools_inventory.md) — flow-derivation history (superseded as spec).
- [reconciliation_audit.md](reconciliation_audit.md) — why we rebuilt.

## Tomorrow — do in order
1. **Review `tool_specs.md` end-to-end** — correct any input/output/guard wording before coding.
2. **Resolve 2 parked decisions:**
   - Chef self-onboarding: Admin/seed-only (no executor) **or** add one `chef_profiles` executor (controlled Foundation unfreeze)?
   - `tools_inventory.md`: keep as history (current) or retire?
3. **Setup checks on the build machine:** Postgres running · Vertex AI ADC creds · pin the exact `gemini-3.6-*` Vertex model id (don't guess).
4. **START BUILD — Step 1: runtime skeleton** (discuss-then-build, one piece at a time):
   - state schema → graph → **Postgres checkpointer** (`AsyncPostgresSaver`) → `interrupt()`/resume → `send_and_await_reply` primitive → `delegate_write` wrapper.
5. **Then Customer spoke — same-domain tools first** (testable standalone, no cross-domain writes): `get_customer_profile`, `register_customer`, `resolve_time_pool`, `find_nearby_kitchens`, `view_chef_menu`, `create_order`, `add_item_to_order`, `view_cart` — each with a test.

## Build order (from the SCC analysis)
Foundation (frozen) → **runtime skeleton + Master core (hub)** → Customer spoke → Payment → Master cutoff engine (scheduled) → Chef spoke → Driver spoke.

## Standing rules (do not forget)
- **Discuss-then-build**, one tool/piece at a time.
- **No AI inside tools** — pure code; guard-then-guide with fixed `if/else` templates.
- **Read any table; write only your own; cross-domain writes go Master→owner executor.**
- **Vertex AI / Gemini 3.6 / GCP / no AWS Bedrock.**
- Foundation (tables + 21 executors) is **frozen** — don't edit without an explicit decision.
