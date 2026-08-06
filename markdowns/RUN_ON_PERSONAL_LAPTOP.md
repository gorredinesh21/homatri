# Running Homaatri on a personal laptop — mock → real checklist

**Why this exists:** on the office laptop everything external is a **mock / stand-in** (GCP is
unreachable, so the LLM is Bedrock/Kimi; payments, maps, DB, and WhatsApp are all faked). On a
personal laptop we can wire the real services. This is the list of every seam to flip, the exact
file / env var, and the non-obvious gotchas.

Legend: 🟢 = single env/config flip · 🟡 = needs setup (keys/account) · 🔴 = real code change.

---

## 1. 🔴 LLM — Bedrock/Kimi → Gemini
**This is the biggest one — not just a key swap.**

- What runs today: [dev_server.py](../dev_server.py) drives the whole agent loop itself with
  **boto3 Bedrock Converse** on `KIMI = "moonshotai.kimi-k2.5"` (`_br` client, `profile_name="homatri-bedrock"`,
  `us-east-1`). Tool schemas are hand-built for Converse in `_toolconfig()`.
- What's already there but **UNUSED**: [app/agents/llm.py](../app/agents/llm.py) is a `ChatVertexAI`
  (Gemini) factory, and `Agent.ainvoke` binds tools the LangChain way. The harness never calls these —
  it only uses each agent's `tool_map`.
- To switch to Gemini, pick one:
  - **(a) Rewire the harness to the LangChain/Gemini path** — replace the `_br.converse(...)` loop in
    `run_agent` with `agent.ainvoke(messages)` (uses `app/agents/llm.py`). Cleanest long-term; means the
    harness and the "real" runtime share one LLM path. Tool-call plumbing (`_toolconfig`, the
    `toolUse`/`toolResult` block handling) gets replaced by LangChain's tool-call objects.
  - **(b) Keep the harness loop, swap the client** — replace boto3 Bedrock with the Gemini SDK and
    re-format tool schemas to Gemini's function-calling shape. More throwaway work.
- Auth: Gemini via Vertex uses **ADC / service account** (no API key) — set
  `GOOGLE_APPLICATION_CREDENTIALS` to the service-account JSON, and `GCP_PROJECT` / `GCP_LOCATION` /
  `GEMINI_MODEL` (already in [app/core/config.py](../app/core/config.py), defaults
  `homatri-503308` / `global` / `gemini-3.6-flash`). Enable the Vertex AI API + billing.
- Remove the AWS dependency: the boto3 client + `homatri-bedrock` profile + `.env` `BEDROCK_MODEL_ID`
  lines become dead once (a) is done.
- ⚠️ Behaviour will shift — the prompts in `_customer_extra` / `_chef_extra` / `_driver_extra` were tuned
  against Kimi. Re-test the flows and tighten prompts for Gemini.

## 2. 🟢 Database — SQLite → PostgreSQL
- Today: [dev_server.py:17](../dev_server.py) forces `DATABASE_URL` default to
  `sqlite+aiosqlite:///./poc.db`; tests use `poc_test.db`. The models run on SQLite for dev.
- The **config default is already Postgres**:
  `postgresql+asyncpg://dinesh:homatri_pass@localhost:5432/homatri_db`
  ([app/core/config.py:21](../app/core/config.py)).
- Steps:
  1. Install + run Postgres locally; create the `homatri_db` database + user.
  2. Set `DATABASE_URL` in `.env` to the asyncpg URL (and **remove/override** the sqlite default set at
     the top of `dev_server.py`).
  3. Ensure `asyncpg` is installed.
  4. Create tables: run one of the seed scripts (`dev_batch.py` / `dev_reset.py`) which call
     `Base.metadata.create_all`, **or** wire up Alembic migrations for a real setup.
- 🟡 Nice-to-have: JSONB, `func.now()` server defaults, and `Numeric` behave more correctly on Postgres
  than SQLite — expect a few dev-only quirks to disappear. `poolclass=NullPool` is fine to keep for dev.

## 3. 🟡 Payments — Razorpay mock → real
- Today: `razorpay_mock_mode=True` ([app/core/config.py:40](../app/core/config.py)); the "gateway" is
  the local `/static/mock_payment.html` page and a manual **💳 Pay** button that POSTs `/pay`, which fires
  the `confirm_payment` / `confirm_topup_payment` resume handlers. Signature verification (HMAC SHA256)
  is already implemented in [payment_service.py](../app/services/payment_service.py).
- Steps:
  1. Razorpay account → get real `RAZORPAY_KEY_ID` / `RAZORPAY_KEY_SECRET` / `RAZORPAY_WEBHOOK_SECRET`
     (test-mode keys are fine to start).
  2. Set them in `.env` and `RAZORPAY_MOCK_MODE=False` (the real API branch also guards against the
     placeholder key id).
  3. 🔴 Give Razorpay a **public callback/webhook URL** — `base_url` is hard-coded
     `http://localhost:8000` ([payment_service.py:40](../app/services/payment_service.py)). Use a tunnel
     (ngrok/cloudflared) or a deployed host, and register the webhook in the Razorpay dashboard.
  4. 🔴 The current `/pay` endpoint is a mock trigger. For real payments, the confirmation must come from
     the **Razorpay webhook** hitting an endpoint that calls `process_payment_webhook` (passing the raw
     body + `signature` so the HMAC check runs). Wire that endpoint.

## 4. 🟡 Maps — mock nearest-neighbour → real Google Routes API
- Today: `google_maps_api_key=""` → [maps_service.py](../app/services/maps_service.py) runs the
  offline nearest-neighbour mock. Real path (`computeRoutes`, `optimizeWaypointOrder`) is already coded
  and degrades gracefully on error.
- Steps: GCP → enable **Routes API** + billing → set `GOOGLE_MAPS_API_KEY` in `.env`. No code change;
  `run_cutoff_batch` will start getting live, traffic-aware routes/ETAs.

## 5. 🟡 WhatsApp — browser widgets → Meta Cloud API
- Today: "WhatsApp" is the multi-widget tester page; inbound = widgets POST Meta-shaped payloads to
  `/webhook`; outbound is written to `system_outbound_queue` and **polled** by the widgets (nothing is
  actually sent). `WEBHOOK_VERIFY_TOKEN = "homatri_verify"` is a placeholder.
- Steps for real messaging: Meta WhatsApp Cloud API creds (phone-number id, permanent token, verify
  token), point Meta's webhook at the public `/webhook`, and add a **sender** that drains
  `system_outbound_queue` via the Cloud API instead of the widget poll. (Out of scope for a local test,
  but it's the last "dummy" to replace for a true deployment.)

---

## `.env` on a personal laptop — target shape
```
# Database
DATABASE_URL=postgresql+asyncpg://<user>:<pass>@localhost:5432/homatri_db

# LLM (Gemini via Vertex)
GCP_PROJECT=homatri-503308
GCP_LOCATION=global
GEMINI_MODEL=gemini-3.6-flash
GOOGLE_APPLICATION_CREDENTIALS=/abs/path/service-account.json

# Payments (real Razorpay)
RAZORPAY_KEY_ID=rzp_test_...
RAZORPAY_KEY_SECRET=...
RAZORPAY_WEBHOOK_SECRET=...
RAZORPAY_MOCK_MODE=False

# Maps
GOOGLE_MAPS_API_KEY=...

# (Bedrock lines no longer needed once the LLM path is Gemini)
```

## Order to do it in
1. **Postgres** (2) — everything else sits on the DB; get it green first.
2. **Gemini** (1) — the harness won't run without a working LLM; do this next and re-test the flows.
3. **Maps** (4) — pure key flip, low risk.
4. **Razorpay** (3) — needs the public-URL/webhook wiring.
5. **WhatsApp** (5) — only for a real (non-widget) deployment.

## Also remember (pre-existing)
- Seed `system_settings` in a real migration: `delivery_fee`, `cutoff_lunch` 11:30 / `cutoff_dinner` 18:30
  (still constants in [app/tools/common.py](../app/tools/common.py)), `timezone` Asia/Kolkata.
