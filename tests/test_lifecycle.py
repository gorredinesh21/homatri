"""Integration + e2e tests driving the real HTTP surface (offline LLM)."""
from __future__ import annotations

from tests.conftest import CHEF, CUSTOMER, DRIVER


def _order(client):
    return client.get("/api/state").json()["orders"][0]


def test_health(client):
    h = client.get("/api/health").json()
    assert h["status"] == "ok"
    assert h["whatsapp_provider"] == "mock"


def test_webhook_verify_handshake(client):
    r = client.get(
        "/webhook/whatsapp",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "HOMAATRI_VERIFY_TOKEN_2026",
            "hub.challenge": "12345",
        },
    )
    assert r.status_code == 200 and r.text == "12345"
    bad = client.get(
        "/webhook/whatsapp",
        params={"hub.mode": "subscribe", "hub.verify_token": "wrong", "hub.challenge": "x"},
    )
    assert bad.status_code == 403


def test_webhook_rejects_bad_signature(client):
    # posting raw to the real webhook without a valid signature must 403
    r = client.post(
        "/webhook/whatsapp",
        content=b'{"object":"whatsapp_business_account","entry":[]}',
        headers={"X-Hub-Signature-256": "sha256=bogus", "Content-Type": "application/json"},
    )
    assert r.status_code == 403


def test_order_creation_offline(place_order):
    o = place_order()
    assert o["status"] == "AWAITING_PAYMENT"
    names = {i["name"]: i["quantity"] for i in o["items"]}
    assert names == {"Butter Roti": 3, "Jeera Rice": 1, "Paneer Butter Masala": 2}
    assert o["total"] == 3 * 20 + 1 * 80 + 2 * 120 + 30  # + delivery fee


def test_offmenu_order_asks_clarification(client):
    client.post("/api/sim/send", json={"phone": CUSTOMER, "text": "i want a pizza and coke"})
    assert client.get("/api/state").json()["orders"] == []


def test_full_lifecycle(client, place_order):
    o = place_order()
    code = o["code"]

    client.post(f"/api/sim/pay/{code}")
    o = _order(client)
    assert o["status"] == "CONFIRMED" and o["payment"]["status"] == "PAID"

    client.post("/api/sim/tap", json={"phone": CHEF, "reply_id": f"cook_start:{code}"})
    assert _order(client)["status"] == "PREPARING"

    # add food mid-order
    client.post("/api/sim/send", json={"phone": CUSTOMER, "text": "please add 2 more butter rotis"})
    o = _order(client)
    assert any(c["status"] == "PENDING" and c["type"] == "FOOD" for c in o["change_requests"])

    client.post("/api/sim/tap", json={"phone": CHEF, "reply_id": f"accept_food:{code}"})
    o = _order(client)
    roti = next(i for i in o["items"] if i["name"] == "Butter Roti")
    assert roti["quantity"] == 5

    client.post("/api/sim/tap", json={"phone": CHEF, "reply_id": f"mark_ready:{code}"})
    o = _order(client)
    assert o["status"] == "READY_FOR_PICKUP"
    assert o["delivery"]["route_url"].startswith("https://www.google.com/maps/dir/")

    # change delivery time -> driver accepts
    client.post("/api/sim/send", json={"phone": CUSTOMER, "text": "change delivery time to 8 30"})
    client.post("/api/sim/tap", json={"phone": DRIVER, "reply_id": f"accept_change:{code}"})
    assert "8:30" in _order(client)["requested_delivery_time"]

    client.post("/api/sim/tap", json={"phone": DRIVER, "reply_id": f"driver_pickup:{code}"})
    assert _order(client)["status"] == "OUT_FOR_DELIVERY"

    client.post("/api/sim/tap", json={"phone": DRIVER, "reply_id": f"driver_delivered:{code}"})
    assert _order(client)["status"] == "DELIVERED"


def test_chef_and_driver_agentic_freetext(client, place_order):
    """Chef/driver advance the order by TYPING, not just tapping buttons."""
    code = place_order()["code"]
    client.post(f"/api/sim/pay/{code}")  # -> CONFIRMED

    # Chef types instead of tapping "Cooking Started"
    client.post("/api/sim/send", json={"phone": CHEF, "text": "starting to cook this now"})
    assert _order(client)["status"] == "PREPARING"

    # Chef types that it's ready
    client.post("/api/sim/send", json={"phone": CHEF, "text": "the order is ready to be picked up"})
    o = _order(client)
    assert o["status"] == "READY_FOR_PICKUP"
    assert o["delivery"] and o["delivery"]["route_url"]

    # Driver types pickup + delivered
    client.post("/api/sim/send", json={"phone": DRIVER, "text": "i picked up the order"})
    assert _order(client)["status"] == "OUT_FOR_DELIVERY"
    client.post("/api/sim/send", json={"phone": DRIVER, "text": "delivered it to the customer"})
    assert _order(client)["status"] == "DELIVERED"


def test_illegal_action_is_safe(client, place_order):
    o = place_order()
    code = o["code"]
    # marking ready before payment/cook should not crash and should not advance
    client.post("/api/sim/tap", json={"phone": CHEF, "reply_id": f"driver_delivered:{code}"})
    assert _order(client)["status"] == "AWAITING_PAYMENT"
