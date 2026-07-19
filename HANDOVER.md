# Homaatri — Handover (branch: `aws-bedrock`)

Agent-first, WhatsApp-native food-ordering platform. The **AI is the runtime**; the
backend is its tool belt. Pairs with [README.md](README.md),
[docs/report/index.html](docs/report/index.html) (AI strategy + QA),
[docs/report/model-eval.html](docs/report/model-eval.html) (model ranking) and
[docs/whatsapp_cloud_api_reference.md](docs/whatsapp_cloud_api_reference.md).

## Status
Production-ready core, verified. **23 pytest tests pass.** Full lifecycle works
end-to-end across customer + chef + driver, driven by an LLM tool-calling agent.

## Architecture (agent-first)
```
inbound msg (customer | chef | driver)
   → Manager Agent  [app/services/conversation.py + agent.py]
       • Context Assembler [context.py]: policy + order state + last-N transcript
         + trio memory (pgvector) + rolling summary — injected EVERY turn
       • Policy layer [policy.py]: lifecycle × action rules, enforced as tool
         pre-conditions (state machine = guardrail, not a second brain)
       • Tool registry (role-scoped) → primitives: DB · payments · whatsapp · routing
   → authoritative replies + cross-role relays + SSE to the 3-phone UI
```

## LLM — Amazon Bedrock
- Config in `app/services/llm.py` (`ChatBedrockConverse`), model ids via env.
- **Active:** `us.meta.llama4-scout-17b-instruct-v1:0` (primary) → `qwen.qwen3-next-80b-a3b` (fallback). Chosen via the model eval (Scout = top score + fastest + cheapest ~$5.7/1k orders).
- **Claude Haiku 4.5** (`us.anthropic.claude-haiku-4-5-20251001-v1:0`) is available on the account once the Anthropic use-case form is submitted (see [docs/report/claude-access.html](docs/report/claude-access.html)) — flip the two `BEDROCK_MODEL_ID` lines in `.env` to use it.
- Credentials: boto3 default chain (`~/.aws/credentials`, IAM user `agy-cli-user`, region `us-east-1`). `LLM_ENABLED=false` → deterministic offline parser (no AWS).

## The tool belt (~30 tools the AI can call)
- **Customer:** get_menu, get_order_status, place_order, add_items, remove_items, note_preference, change_delivery_time, change_delivery_address, cancel_order, track_order, get_customer_history, repeat_last_order, check_payment_status, rate_order, get_kitchen_status, escalate_to_human
- **Chef:** start_cooking, mark_ready, accept_food_change, reject_food_change, mark_item_sold_out, restock_item, set_kitchen_open, set_prep_estimate, get_order_queue, send_message_to_customer, escalate_to_human, get_order
- **Driver:** mark_picked_up, mark_delivered, accept_delivery_change, report_delay, update_location, report_delivery_issue, request_reassignment, send_message_to_customer, escalate_to_human, get_order

## Key flows (verified)
- **Order → pay → bill:** pay screen shows the **amount only**; the itemised bill is sent as a WhatsApp text **after** payment. Amounts are always system-authored (never the model's guess).
- **Add while cooking → top-up:** chef accepts → customer is asked to pay **just the extra** → after they pay, the **chef gets the final updated summary**. (`amount_paid`/`balance_due` on the order.)
- **Preferences:** "no garlic" etc. → `note_preference` (recorded + relayed to chef), never an order change.
- **Cancel/refund, sold-out, kitchen open/close, prep ETA, delay, escalate** — all live.
- Payment webhook is **idempotent**; agent loop is hardened (dedup identical tool calls, force final answer, no raw-tool-use leaks).

## Run locally (no Docker)
```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements-dev.txt
# with Bedrock (needs ~/.aws creds):
.venv/Scripts/python -m uvicorn app.main:app --port 8000
# or fully offline (deterministic, no AWS):
DATABASE_URL="sqlite+aiosqlite:///./homatri.db" LLM_ENABLED=false .venv/Scripts/python -m uvicorn app.main:app
```
Open http://localhost:8000 → **Reset Demo** → drive the 3 phones. Tests: `.venv/Scripts/python -m pytest`.

---

## 🐳 FOR ANTIGRAVITY — dockerize the whole project **freshly**
The existing Docker files are stale (HF-era) — **ignore them and build the containerization from scratch** for the current stack.

**Stack to containerize**
- App: async **FastAPI**, ASGI entrypoint `app.main:app` (uvicorn). On startup it ensures pgvector, auto-creates tables, and seeds demo data.
- DB: **PostgreSQL 16 with pgvector** (use image `pgvector/pgvector:pg16`).
- LLM: **Amazon Bedrock** via `langchain-aws` + `boto3` (all deps already in `requirements.txt`).
- Python 3.12.

**Must-haves**
1. **Multi-service `docker compose`**: `db` (pgvector/pg16, healthcheck `pg_isready`, named volume) + `app` (build from Dockerfile, `depends_on: db healthy`, port 8000).
2. **App env** (see `.env.example` for the full list): `DATABASE_URL=postgresql+asyncpg://homatri:homatri@db:5432/homatri`, `AWS_REGION`, `BEDROCK_MODEL_ID`, `BEDROCK_FALLBACK_MODEL_ID`, `BEDROCK_EMBEDDING_MODEL_ID`, `LLM_ENABLED=true`, `WHATSAPP_PROVIDER=mock`, `PAYMENT_PROVIDER=demo`, `PUBLIC_BASE_URL=http://localhost:8000`, `WEB_CONCURRENCY=1`, plus `RAZORPAY_*`/`META_*` when going live.
3. **AWS credentials:** pass via env (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`) from the host `.env` — **never bake creds into the image**. Offer a commented alternative: mount host `~/.aws` read-only.
4. **Single uvicorn worker** (`WEB_CONCURRENCY=1`): the SSE event bus and the mock WhatsApp provider hold in-process state. Horizontal scale needs Redis (documented, not yet built).
5. Non-root user, `/api/health` healthcheck, an entrypoint that waits for the DB then launches uvicorn. Keep the image lean (`python:3.12-slim`; only `curl` needed as an extra system pkg).
6. `docker compose up --build` must come up clean and serve http://localhost:8000.
7. Do **not** copy `aws_credentials.zip`, `.env`, `.venv`, `*.db`, or `tests/` into the image (`.dockerignore`).

---

## ⚠️ Security TODO (do before public)
- **Rotate the IAM key** (`agy-cli-user`) — it has passed through a zip + git history.
- **Remove `aws_credentials.zip`** from the repo AND purge it from git history (`git filter-repo`/BFG). Supply creds via env / `~/.aws` only.
- `.env` is git-ignored; never commit secrets.

## Notable AWS facts
- Bedrock model access checked via `bedrock.get_foundation_model_availability`. Claude/Llama/Qwen/Kimi/GLM/Nova all authorized on this account.
- **Credit balance is not exposed by any API** — read it in Billing → Credits (console). Set a Budget alert (none configured yet).
- GPU self-hosting / SageMaker fine-tuning options + costs are analysed in [docs/report/index.html](docs/report/index.html).
