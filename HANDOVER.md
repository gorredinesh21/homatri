# Homaatri — Handover

Snapshot for resuming work (e.g. on another laptop with Docker). Pairs with
[README.md](README.md) (architecture + full detail) and
[docs/whatsapp_cloud_api_reference.md](docs/whatsapp_cloud_api_reference.md).

## Status: production build complete, verified
The POC was rebuilt into a production-ready async FastAPI package (`app/`).
- **21 pytest tests pass** (units + full-lifecycle e2e + RAG), on SQLite, network-free.
- **Live HTTP smoke test passed** — real uvicorn: order → signed payment webhook → CONFIRMED/PAID; SSE streamed `state` / `wa_message` / `wa_status`.
- Full lifecycle works: order → pay → chef cooks → mid-order food add → driver dispatch (TSP route + location pin) → time change → delivered.

## ⚠️ Environment note — Docker/Postgres not run here
> Docker/Postgres couldn't be run here — no container runtime on this laptop. So I compile-verified the pgvector SQL for the Postgres dialect and ran everything else live on SQLite. First `docker compose up` on any Docker host will exercise the real Postgres path (auto-create builds the schema; Alembic baseline is one `alembic revision --autogenerate` away).

## Run it

### Option A — Docker (real Postgres + pgvector) — do this on the Docker laptop
```bash
cp .env.example .env        # add HF_TOKEN (optional; offline parser works without)
docker compose up --build
# → http://localhost:8000
```
First boot auto-creates the schema and seeds demo data. To generate the real
Alembic baseline once Postgres is up:
```bash
docker compose exec app alembic revision --autogenerate -m "baseline"
docker compose exec app alembic upgrade head
```

### Option B — Local, no Docker (SQLite)
```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements-dev.txt   # Scripts→bin on mac/linux
DATABASE_URL="sqlite+aiosqlite:///./homatri.db" \
  .venv/Scripts/python -m uvicorn app.main:app --reload --port 8000
```

Open **http://localhost:8000** → **Reset Demo** → drive the three phones.

## Demo phones (seeded)
- Customer `+919876543210` (Rohan/Dinesh) · Chef `+919999888877` (Kiran, Sharma's Kitchen) · Driver `+918888777766` (Suresh)
- Menu: Butter Roti ₹20, Paneer Butter Masala ₹120, Dal Fry ₹90, Chapati ₹15, Jeera Rice ₹80.

## Demo script
1. Customer: `hey my name is dinesh, i need 3 butter rotis, 1 jeer rice and 2 paaaneer batter musala` → payment link.
2. Open link → **Secure Pay** → CONFIRMED, chef notified.
3. Chef: **Cooking Started**.
4. Customer: `can you add 2 more butter rotis` → chef **Accept Change** → total updates.
5. Chef: **Ready for Pickup** → rider gets location pin + Maps route.
6. Customer: `change the time of delivery, i want it at 8 30` → rider **Accept Time Change**.
7. Driver: **Accept & Picked Up** → **Mark Delivered**.

## TODO before public / production
- [ ] **Rotate the HF token** — it was committed in the old `.env` (still in git history). `.env` is now git-ignored.
- [ ] Generate the Alembic baseline against Postgres (command above), then set `HOMATRI_AUTO_CREATE=false`.
- [ ] Go live on Meta: `WHATSAPP_PROVIDER=meta` + `META_ACCESS_TOKEN`/`META_PHONE_NUMBER_ID`/`META_APP_SECRET`; point Meta webhook to `https://<host>/webhook/whatsapp` (verify token = `WHATSAPP_VERIFY_TOKEN`).
- [ ] Go live on Razorpay: `PAYMENT_PROVIDER=razorpay` + `RAZORPAY_KEY_ID/SECRET/WEBHOOK_SECRET`; webhook → `https://<host>/webhook/payment`.
- [ ] For horizontal scale: move SSE bus + inbound queue to Redis (seams in `services/events.py`, `workers/dispatcher.py`), then raise `WEB_CONCURRENCY` (currently pinned to 1 because the mock provider + SSE bus hold in-process state).

## Key facts
- LLM: HF Router, primary `meta-llama/Llama-3.1-8B-Instruct`, fallback `Qwen/Qwen3-8B`. The 70B / Qwen2.5-7B return **403** on this HF account (providers not enabled) — 8B is the working + smaller choice. `LLM_ENABLED=false` → deterministic offline parser.
- Providers are config-swappable: `WHATSAPP_PROVIDER=mock|meta`, `PAYMENT_PROVIDER=demo|razorpay`.
- Tests: `.venv/Scripts/python -m pytest`.
