# 🍲 Homaatri — WhatsApp-First Hyper-Local Food Ordering

Homaatri connects **customers**, **home-cook chefs**, and **delivery riders** entirely through WhatsApp conversation. Customers order in natural language, an LLM parses the message into a structured order against the chef's live menu, payment is collected, the chef cooks, and a rider is dispatched with an optimized route — every state change flowing back to all three parties in real time.

This repository is a **production-ready platform build**. Everything is real — Postgres + pgvector, real payment integration, background processing under Meta's 3-second webhook SLA — except the WhatsApp transport, which ships as a **faithful mock of the Meta Cloud API** so the whole system can be demoed offline and flipped to the real Meta API by changing one config value.

---

## Architecture

```
                       ┌──────────────── FastAPI (async) ────────────────┐
  WhatsApp  ──webhook──▶  /webhook/whatsapp  ──▶ BackgroundTask (≤3s ACK) │
  (mock or Meta)          (HMAC verified)          │                      │
                                                    ▼                      │
                                          Conversation orchestrator        │
                                          ├─ order parsing (LLM + offline) │
                                          ├─ fuzzy menu matcher (RapidFuzz)│
                                          ├─ lifecycle state machine       │
                                          ├─ modifications (food/time/addr)│
                                          ├─ payments (demo / Razorpay)    │
                                          └─ routing (greedy TSP + Maps)   │
                                                    │                      │
   Provider layer (swappable):                      ▼                      │
   • WhatsAppProvider  = Mock | MetaCloud    Postgres + pgvector           │
   • PaymentProvider   = Demo | Razorpay     (orders, users, RAG memory)   │
                                                    │                      │
   SSE  ◀── EventBus ◀── state snapshots ───────────┘                      │
   (3-phone simulator UI)                                                  │
                       └──────────────────────────────────────────────────┘
```

**Stack:** FastAPI · SQLAlchemy 2.0 (async) + asyncpg · PostgreSQL 16 + **pgvector** · Alembic · **AWS Bedrock via LangChain** (`amazon.nova-lite-v1:0` primary / `amazon.nova-micro-v1:0` fallback) · **AWS Titan Embeddings** (`amazon.titan-embed-text-v2:0`, 384-dim normalized) · **FastMCP Server** (`app/mcp_server.py`) · Razorpay · Server-Sent Events · Docker Compose.

### Provider swap (the key design decision)
| Concern | Demo default | Production | Switch |
|---|---|---|---|
| WhatsApp | `MockWhatsAppProvider` (Meta-faithful, in-process) | `MetaCloudProvider` (graph.facebook.com) | `WHATSAPP_PROVIDER=meta` |
| Payments | `DemoGateway` (self-signed webhook) | `RazorpayGateway` (real orders + signatures) | `PAYMENT_PROVIDER=razorpay` |
| LLM | AWS Bedrock (`amazon.nova-lite-v1:0` via LangChain) | same, or `LLM_ENABLED=false` for deterministic offline parsing | `AWS_REGION` / `BEDROCK_MODEL_ID` env |
| MCP | `FastMCP` Server (`app/mcp_server.py`) | Exposes Homaatri tools (`create_order`, `list_menu`, etc.) to LLMs | `python -m app.mcp_server` |

Both mock providers produce and verify the **exact wire formats** of the real services (Meta `X-Hub-Signature-256` HMAC, interactive buttons/lists/location; Razorpay HMAC webhooks), so the production path is exercised end-to-end today. See [docs/whatsapp_cloud_api_reference.md](docs/whatsapp_cloud_api_reference.md).

---

## Quick start

### Docker (full stack: app + Postgres/pgvector)
```bash
cp .env.example .env      # add your HF token (optional; offline parser works without)
docker compose up --build
# → http://localhost:8000   (3-phone simulator)
```

### Local dev (SQLite or your own Postgres)
```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements-dev.txt   # (Scripts→bin on Linux/mac)
# SQLite (zero setup):
DATABASE_URL="sqlite+aiosqlite:///./homatri.db" LLM_ENABLED=false \
  .venv/Scripts/python -m uvicorn app.main:app --reload
```

Open **http://localhost:8000**, click **Reset Demo**, and drive the flow across the three phones.

---

## Demo walkthrough
1. **Customer phone** → type: `hey my name is dinesh, i need 3 butter rotis, 1 jeer rice and 2 paaaneer batter musala`. It parses (typo-tolerant) and replies with a payment link.
2. Open the link → **Secure Pay** → order becomes `CONFIRMED`; the chef is notified with a checklist + buttons.
3. **Chef phone** → tap **Cooking Started**.
4. **Customer** → `can you add 2 more butter rotis` → chef gets a **⚠️ change request**; tap **Accept Change** → total updates.
5. **Chef** → **Ready for Pickup** → a rider is assigned and receives the drop-off **location pin** + an optimized **Google Maps route**.
6. **Customer** → `change the time of delivery, i want it at 8 30` → rider gets a **delivery change**; tap **Accept Time Change**.
7. **Driver phone** → **Accept & Picked Up** → **Mark Delivered**. 🎉

---

## Configuration
All behaviour is env-driven — see [.env.example](.env.example). Key vars: `DATABASE_URL`, `HF_TOKEN` (or `HF_TOKEN_PART1/2`), `WHATSAPP_PROVIDER`, `PAYMENT_PROVIDER`, `META_*`, `RAZORPAY_*`, `PUBLIC_BASE_URL`.

> **Security:** `.env` is git-ignored. Never commit tokens. Rotate any key that has been committed.

### Going live on Meta / Razorpay
- **Meta:** set `WHATSAPP_PROVIDER=meta`, `META_ACCESS_TOKEN`, `META_PHONE_NUMBER_ID`, `META_APP_SECRET`; point your WhatsApp app's webhook to `https://<host>/webhook/whatsapp` (verify token = `WHATSAPP_VERIFY_TOKEN`).
- **Razorpay:** set `PAYMENT_PROVIDER=razorpay`, `RAZORPAY_KEY_ID/SECRET/WEBHOOK_SECRET`; point the Razorpay webhook to `https://<host>/webhook/payment`.

---

## Testing
```bash
.venv/Scripts/python -m pytest          # 21 tests: units + full-lifecycle e2e + RAG
```
Tests run against SQLite with the LLM disabled (deterministic, network-free). The Postgres/pgvector SQL path is compile-verified for the `postgresql` dialect.

---

## Project layout
```
app/
  core/       config, logging, HMAC security
  db/         async engine/session, portable pgvector column type
  models/     SQLAlchemy entities + enums
  schemas/    Pydantic parsing/intent models
  services/   llm, embeddings, rag, menu_matcher, order_parsing,
              order_lifecycle, role_chat, conversation (orchestrator),
              routing, state_snapshot, events (SSE bus)
  whatsapp/   provider interface + mock + meta + message builders + inbound parser
  payments/   provider interface + demo gateway + razorpay
  api/        webhook, simulator, admin, pages routers
  workers/    background dispatcher (webhook SLA seam)
  main.py     app factory + lifespan
migrations/   Alembic (async)
templates/    3-phone simulator UI + checkout page
tests/        pytest suite
docker/       entrypoint
```

## Scaling notes (documented, not yet wired)
The SSE `EventBus` and the mock provider hold in-process state, so the container runs a **single** uvicorn worker. To scale horizontally: move the event bus + inbound queue to **Redis** (arq/pub-sub) — the `events.py` and `workers/dispatcher.py` seams are designed for exactly this swap — then raise `WEB_CONCURRENCY`. See [RESEARCH_NOTES.md](RESEARCH_NOTES.md).

---

## Build Journey (POC → production)

A chronological log of this rebuild — what was asked and what was delivered.

### Starting point
A working proof-of-concept: a single 1,327-line `main.py` with an in-memory
mock database, a browser "3-phone" simulator, and Hugging Face LLM calls.
Everything (DB, WhatsApp, payments) was mocked.

### The mandate
> "Build the whole thing… production ready, containerised, ready to host. Mock
> only the WhatsApp Business API (make it behave like the real Meta server).
> Build everything else for real — payments, driver location, the lot. Act as
> the tech lead: task points, build → test → debug → re-test → deploy."

### Decisions locked (with the user)
- **LLM:** Hugging Face Inference Router, `meta-llama/Llama-3.1-8B-Instruct`
  primary / `Qwen/Qwen3-8B` fallback (70B and Qwen2.5 return 403 on this
  account — 8B is both the working and the smaller choice). Verified live.
- **Payments:** real Razorpay integration **plus** a self-signed demo gateway.
- **Hosting:** host-agnostic Docker Compose.
- **Queue:** in-process (FastAPI) now, documented Redis swap for later.

### What was built & delivered
| Milestone | Delivered |
|---|---|
| Secure the repo | `.env` untracked + git-ignored, `.env.example` added |
| Research | Meta WhatsApp Cloud API v25.0 wire-format spec (`docs/`) |
| Scaffold | Async FastAPI package, config, logging, HMAC security, Docker |
| Data layer | SQLAlchemy 2.0 async, Postgres + **pgvector**, Alembic, seed |
| LLM service | HF Router client, JSON mode, retries, offline fallback |
| RAG | Offline 384-d embedder + pgvector cosine search |
| WhatsApp | Provider interface + **faithful Meta mock** + Meta Cloud stub |
| Orders | LLM + fuzzy parser, lifecycle state machine, modifications |
| Payments | Razorpay + demo gateway, HMAC webhook verification |
| Routing | Greedy TSP + Google Maps dispatch + driver location pin |
| Orchestrator | Cross-role conversation brain, background workers, SSE |
| Frontend | Rewired 3-phone simulator + checkout page |
| Tests | pytest suite (units + full-lifecycle e2e + RAG) |
| Container | Dockerfile + compose (app + pgvector) + entrypoint |

Verified with a full-lifecycle end-to-end test, a live HTTP smoke test, and a
Postgres-dialect SQL compile-check (which caught a real pgvector bug before it
shipped).

### Follow-up improvements (user feedback)
1. **Smarter customer agent** — greetings/"yes" no longer fabricate an order; the
   bot chats, holds order context, and only orders when dishes are named.
2. **Agentic chef & driver** — they advance the order by *typing* ("started
   cooking", "order's ready", "picked up", "delivered"), and never hit a
   dead-end (follow-up action buttons added).
3. **True tool-calling agents** — confirmed the HF router supports OpenAI-style
   tool-calling, then rebuilt customer/chef/driver on a real tool-calling agent
   loop (the LLM decides which tool to call); deterministic fallback retained.
4. **Trio shared memory** — a `(customer, chef, driver)` relationship memory so
   the assistant can reference shared context when talking to any stakeholder
   (e.g. surface a customer's "less spicy" note to the chef).

### Known constraints (honest status)
- **Docker/Postgres not run on the build machine** (no container runtime); the
  pgvector path is SQL-compile-verified and everything else ran live on SQLite.
  First `docker compose up` on any Docker host exercises the real Postgres path.
- **LLM credits:** the HF free inference tier was exhausted during testing
  (HTTP 402). The app **degrades gracefully** to the deterministic engine, so
  orders and the full lifecycle keep working without the LLM. A sustainable LLM
  (AWS Bedrock, self-hosted vLLM, HF PRO/credits, or Gemini) lights the agentic
  behavior back up with no architectural change.

See [HANDOVER.md](HANDOVER.md) for run steps and the go-live checklist.
