# Homaatri — Handover

Snapshot for resuming work (e.g. on another laptop with Docker). Pairs with
[README.md](README.md) (architecture + full detail) and
[docs/whatsapp_cloud_api_reference.md](docs/whatsapp_cloud_api_reference.md).

## Status: production build complete, verified
The POC was rebuilt into a production-ready async FastAPI package (`app/`) on branch `aws-bedrock`.
- **23 pytest tests pass** (units + full-lifecycle e2e + RAG + Bedrock / Titan Embeddings), network-free on SQLite.
- **AWS Bedrock LLM & Titan Embeddings via LangChain** (`langchain-aws==0.2.35`):
  - Primary LLM: `amazon.nova-lite-v1:0`
  - Fallback LLM: `amazon.nova-micro-v1:0`
  - Embeddings: `amazon.titan-embed-text-v2:0` (384-dim projected & normalized for `pgvector`).
- **FastMCP Server** (`app/mcp_server.py`):
  - Exposes 5 core website tools to external/internal LLM agents (`list_menu`, `get_order_status`, `create_order`, `update_order_address`, `simulate_payment`).
- **3-Way AI Mediator Agent Architecture**:
  - Inter-role relay tools (`send_message_to_customer`, `notify_chef`, `notify_driver`).
  - Shared Trio Relationship Memory (`RelationshipMemory` table + pgvector search).
  - 3-level prompt context structure (Live Order State + Shared Trio History + Role History).
- **In-Flight Top-Up Payment & Link Sanitization**:
  - Auto-calculates price delta (`new_total - paid_amount`) on in-flight add-ons and generates top-up payment links.
  - Programmatic reasoning tag cleanup via `clean_llm_response()`.

## 🔒 Encrypted AWS Credentials
An encrypted zip containing the AWS credentials for `agy-cli-user` (Account `187516374608`) has been saved in the project root:
- **File:** `aws_credentials.zip`
- **Password:** `THORkills@21`

To extract credentials:
```bash
7z x -p"THORkills@21" aws_credentials.zip
```

## Run it

### Option A — Docker (real Postgres + pgvector + AWS Bedrock)
```bash
cp .env.example .env
docker compose up --build
# → http://localhost:8000
```
The Docker container mounts `~/.aws` automatically and passes AWS environment variables to Bedrock.

### Option B — Local, no Docker (SQLite)
```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt   # Scripts→bin on mac/linux
DATABASE_URL="sqlite+aiosqlite:///./homatri.db" \
  .venv/Scripts/python -m uvicorn app.main:app --reload --port 8000
```

Open **http://localhost:8000** → **Reset Demo** → drive the three phones.

## Key facts
- **LLM:** AWS Bedrock (`amazon.nova-lite-v1:0` via LangChain). `LLM_ENABLED=false` → deterministic offline parser.
- **MCP:** FastMCP Server (`app/mcp_server.py`).
- **Providers:** config-swappable (`WHATSAPP_PROVIDER=mock|meta`, `PAYMENT_PROVIDER=demo|razorpay`).
- **Tests:** `pytest`.
