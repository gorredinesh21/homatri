"""Pytest fixtures. Runs the app against a throwaway SQLite DB with the LLM
disabled (deterministic offline parsing) so tests are fast and network-free."""
from __future__ import annotations

import os
import pathlib

# Must be set BEFORE importing app (settings is cached at import).
_DB = pathlib.Path(__file__).parent / "_test.db"
os.environ.update(
    DATABASE_URL=f"sqlite+aiosqlite:///{_DB.as_posix()}",
    LLM_ENABLED="false",
    WHATSAPP_PROVIDER="mock",
    PAYMENT_PROVIDER="demo",
    META_APP_SECRET="test_app_secret",
    PUBLIC_BASE_URL="http://testserver",
    APP_ENV="development",
)

import pytest  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

CUSTOMER = "+919876543210"
CHEF = "+919999888877"
DRIVER = "+918888777766"


@pytest.fixture(scope="session")
def client():
    if _DB.exists():
        _DB.unlink()
    from app.main import app

    with TestClient(app) as c:
        yield c
    if _DB.exists():
        _DB.unlink()


@pytest.fixture(autouse=True)
def _reset(client):
    client.post("/api/reset")
    yield


@pytest.fixture
def place_order(client):
    def _place(text: str = "hi im dinesh, i need 3 butter rotis, 1 jeera rice and 2 paneer butter masala"):
        client.post("/api/sim/send", json={"phone": CUSTOMER, "text": text, "profile_name": "Dinesh"})
        orders = client.get("/api/state").json()["orders"]
        return orders[0]

    return _place
