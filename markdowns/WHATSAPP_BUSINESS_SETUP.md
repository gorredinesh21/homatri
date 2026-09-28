# Homaatri WhatsApp Ordering — LIVE Setup & Runbook (Sep 2026)

> Status: **FULLY CONFIGURED & LIVE**. One customer agent on WhatsApp handles
> registration, menus, ordering, payment and tracking. Chefs and riders use
> their web portals (homatri.com/chef, homatri.com/rider) — they are NOT on
> WhatsApp anymore. The multi-agent (Chef/Driver/Master WhatsApp personas)
> design was retired on 2026-09-28.

## What is live

| Piece | Value |
|---|---|
| WhatsApp number | **+91 83693 84157** ("Homatri") |
| Meta App | Homatri — ID `1617392946483287` (mode: **In development**) |
| Business portfolio | `945324205253894` |
| WABA | Homatri — `1728982918324573` |
| Phone Number ID | `1327018387151457` (quality GREEN, Cloud API) |
| Access token | Permanent **System User** token (`expires_at=0`), scopes: whatsapp_business_messaging/management + business_management. Stored in repo `.env` (gitignored) + Cloud Run env `META_WHATSAPP_TOKEN` |
| Webhook URL | `https://homatri-backend-195132182954.us-central1.run.app/webhook` (= api.homatri.com/webhook, same service) |
| Webhook verify token | `homatri_verify` (`WEBHOOK_VERIFY_TOKEN` env) |
| Webhook fields | `messages` (subscribed) |
| Payments | REAL Razorpay (live keys on Cloud Run). Payment links minted per order; confirmation via poller (`services/payment_confirmation.py`), `/payments/razorpay` webhook, `/payments/check` and the `/pay/thanks` page ping |

## The one agent

- Defined in `backend/app/agents/agents.py` → `customer_agent` (14 tools).
- Prompt: `backend/app/agents/prompts.py` (`CUSTOMER_AGENT_PROMPT`).
- Every inbound message routes to it (`backend/app/api/server.py::_agent_runner_factory`).
- Long waits (location pin, payment) use the pause/resume router (`backend/app/router.py` + `tools/pause.py`).

## Message flow (customer journey)

1. Customer messages +91 83693 84157 → Meta POSTs `/webhook` → parse → dedup (wamid, 300s) → background task.
2. Router: pending pause? resume it. Otherwise → customer agent (Gemini 2.5 Flash Lite on Vertex AI, tool loop ≤6 turns).
3. Reply → logged (conversation_messages) → sent via Graph API v21.0.
4. `request_payment` mints a real Razorpay link → pause PAYMENT_CONFIRM (out-of-band).
5. Customer pays → **any** of: 15s poller / Razorpay webhook / thanks-page ping → settle (server-side Razorpay fetch is the only truth) → order CONFIRMED → WhatsApp confirmation.
6. Portal actions (kitchen packed, rider pickup, delivery PIN) enqueue in `system_outbound_queue` → the outbox dispatcher sends them to the customer's WhatsApp.

## Testing recipe (no phone needed)

POST Meta-shaped JSON to the prod webhook (fresh `wamid` each time — 300s dedup):

```bash
curl -s https://api.homatri.com/webhook -H 'Content-Type: application/json' -d '{
  "entry":[{"changes":[{"value":{"messages":[
    {"from":"917416767453","id":"wamid.TEST<random>","type":"text","text":{"body":"Hi"}}]}}]}]}'
```

⚠️ With live Meta creds set on Cloud Run this **sends a real WhatsApp reply** to that number.
Verify replies: `GET https://api.homatri.com/outbox?phone=7416767453` (10-digit phone).

## Known limits (app in development mode)

- Only **allowed recipients** (added under WhatsApp → API Setup) can receive messages — currently Dinesh's numbers. Customers messaging IN always get replies (24h window) but **business-initiated** messages to non-allowed numbers fail with error 131047 (outbox records FAILED).
- To open to all customers: App Review → make app **Live** + Business Verification (Section 10 checklist in git history of this file) + a payment method on the WABA.
- One Cloud Run instance (`--max-instances 1`, gunicorn `-w 1`): the pause store and background loops are in-memory. Fine at pilot scale.

## Operations

- Deploy: `gcloud run deploy homatri-backend --source . --region us-central1 --project homatri-503308 --account errog2107@gmail.com` (from repo ROOT — Dockerfile is at root).
- Rotate the Meta token: Business Settings → System Users → generate token (whatsapp_business_messaging + whatsapp_business_management), then update Cloud Run env `META_WHATSAPP_TOKEN` AND repo `.env`.
- Razorpay dashboard webhook (optional, instant push): point `https://api.homatri.com/payments/razorpay` at event `payment_link.paid` and set `RAZORPAY_WEBHOOK_SECRET` env to the dashboard secret. The poller + thanks-page ping already work without it.
