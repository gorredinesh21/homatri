"""Tests for the WhatsApp webhook parser + the check-first router (no LLM/DB)."""

from datetime import datetime, timedelta

import pytest

from app.api.whatsapp import normalize_phone, parse_webhook, verify_challenge
from app.router import route
from app.tools.pause import RESUME_HANDLERS, _pending, clear_pending


def _wa_text(from_, body):
    return {"entry": [{"changes": [{"value": {"messages": [
        {"from": from_, "type": "text", "text": {"body": body}}]}}]}]}


def _wa_location(from_, lat, lng):
    return {"entry": [{"changes": [{"value": {"messages": [
        {"from": from_, "type": "location", "location": {"latitude": lat, "longitude": lng}}]}}]}]}


# ---- phone normalization ----

def test_normalize_phone():
    assert normalize_phone("917416767453") == "7416767453"
    assert normalize_phone("+91 74167 67453") == "7416767453"
    assert normalize_phone("7416767453") == "7416767453"


# ---- webhook parsing ----

def test_parse_text():
    assert parse_webhook(_wa_text("917416767453", "hi")) == {
        "phone": "7416767453", "type": "text", "text": "hi"}


def test_parse_location():
    msg = parse_webhook(_wa_location("917000000000", 19.1, 73.0))
    assert msg["phone"] == "7000000000"
    assert msg["type"] == "location"
    assert msg["location"] == {"latitude": 19.1, "longitude": 73.0}


def test_parse_status_callback_ignored():
    assert parse_webhook({"entry": [{"changes": [{"value": {"statuses": [{"id": "x"}]}}]}]}) is None


def test_verify_challenge():
    assert verify_challenge("subscribe", "tok", "12345", "tok") == "12345"
    assert verify_challenge("subscribe", "wrong", "12345", "tok") is None


# ---- router ----

@pytest.mark.asyncio
async def test_route_to_agent_when_no_pending():
    clear_pending("9000000001")
    async def fake_agent(phone, text):
        return {"reply": f"agent got: {text}", "await_location": False}
    out = await route({"phone": "9000000001", "type": "text", "text": "hello"}, fake_agent)
    assert out["reply"] == "agent got: hello"


@pytest.mark.asyncio
async def test_route_resume_on_location():
    async def fake_resume(phone, reply, ctx):
        return f"resumed with {reply['latitude']}"
    RESUME_HANDLERS["_test_resume"] = fake_resume
    _pending["9000000002"] = {"await_type": "LOCATION_PIN", "resume": "_test_resume", "ctx": {},
                              "prompt": "x", "expires_at": datetime.now() + timedelta(minutes=5)}
    async def fake_agent(phone, text):
        return {"reply": "AGENT (should not be called)", "await_location": False}
    out = await route({"phone": "9000000002", "type": "location", "location": {"latitude": 19.1, "longitude": 73.0}}, fake_agent)
    assert out["reply"] == "resumed with 19.1"
    assert "9000000002" not in _pending      # note cleared after resume


@pytest.mark.asyncio
async def test_route_new_message_wins():
    _pending["9000000003"] = {"await_type": "LOCATION_PIN", "resume": "x", "ctx": {},
                              "prompt": "x", "expires_at": datetime.now() + timedelta(minutes=5)}
    async def fake_agent(phone, text):
        return {"reply": "agent handled fresh request", "await_location": False}
    out = await route({"phone": "9000000003", "type": "text", "text": "never mind"}, fake_agent)
    assert out["reply"] == "agent handled fresh request"
    assert "9000000003" not in _pending      # pending dropped — new message wins


@pytest.mark.asyncio
async def test_route_payment_await_survives_off_topic_text():
    # PAYMENT_CONFIRM is out-of-band (resumed by /pay), so a user text must NOT drop it.
    _pending["9000000005"] = {"await_type": "PAYMENT_CONFIRM", "resume": "confirm_payment", "ctx": {},
                              "prompt": "pay", "expires_at": datetime.now() + timedelta(minutes=5)}
    async def fake_agent(phone, text):
        return {"reply": "agent replied while payment pending", "await_location": False}
    out = await route({"phone": "9000000005", "type": "text", "text": "did it go through?"}, fake_agent)
    assert out["reply"] == "agent replied while payment pending"
    assert "9000000005" in _pending           # payment await preserved
    clear_pending("9000000005")


@pytest.mark.asyncio
async def test_route_timeout_rollback():
    _pending["9000000004"] = {"await_type": "LOCATION_PIN", "resume": "x", "ctx": {},
                              "prompt": "x", "expires_at": datetime.now() - timedelta(minutes=1)}  # expired
    async def fake_agent(phone, text):
        return {"reply": "AGENT", "await_location": False}
    out = await route({"phone": "9000000004", "type": "location", "location": {"latitude": 1, "longitude": 2}}, fake_agent)
    assert out.get("expired") is True
    assert "9000000004" not in _pending
