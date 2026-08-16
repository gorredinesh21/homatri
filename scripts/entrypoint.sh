#!/bin/bash
set -e

echo "🚀 Homaatri Container Entrypoint Starting..."

# Ensure PYTHONPATH includes root directory
export PYTHONPATH="/app:${PYTHONPATH}"

# Execute Database Auto-Seeding & Tables Setup in background
echo "⚡ Running Database Auto-Seeding & Schema Setup..."
(python3 backend/dev_seed.py || echo "⚠️ Database seed notice") &

# Set default PORT if not set by Cloud Run
PORT="${PORT:-8000}"

echo "🟢 Launching Production ASGI Gunicorn Server on Port ${PORT}..."
exec gunicorn -w 2 -k uvicorn.workers.UvicornWorker backend.app.api.server:app --bind "0.0.0.0:${PORT}" --timeout 120 --log-level info
