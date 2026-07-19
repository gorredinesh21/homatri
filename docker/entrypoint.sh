#!/usr/bin/env bash
set -euo pipefail

echo "[entrypoint] Homaatri starting (wa=${WHATSAPP_PROVIDER:-mock} pay=${PAYMENT_PROVIDER:-demo})"

# Wait for Postgres to accept connections before booting the app.
if [[ "${DATABASE_URL:-}" == *"postgres"* ]]; then
  echo "[entrypoint] waiting for database..."
  python - <<'PY'
import os, time, socket, re
url = os.environ.get("DATABASE_URL", "")
m = re.search(r"@([^:/]+):(\d+)/", url)
host, port = (m.group(1), int(m.group(2))) if m else ("db", 5432)
for i in range(60):
    try:
        with socket.create_connection((host, port), timeout=2):
            print(f"[entrypoint] database up at {host}:{port}")
            break
    except OSError:
        time.sleep(1)
else:
    raise SystemExit("[entrypoint] database not reachable")
PY
fi

# Run migrations if any exist; otherwise the app's startup create_all handles it.
if [[ -d migrations/versions ]] && compgen -G "migrations/versions/*.py" > /dev/null; then
  echo "[entrypoint] applying alembic migrations"
  alembic upgrade head || echo "[entrypoint] alembic failed; falling back to auto-create"
fi

# Default to 1 worker: the SSE event bus and the mock WhatsApp provider hold
# in-process state, so realtime + mock mode require a single process. For
# horizontal scale, move the bus/queue to Redis (documented swap) and raise
# WEB_CONCURRENCY.
WORKERS="${WEB_CONCURRENCY:-1}"
echo "[entrypoint] launching uvicorn ($WORKERS worker(s))"
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers "$WORKERS"
