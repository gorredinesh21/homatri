"""Pure-unit tests for the building blocks (no app/DB needed)."""
from __future__ import annotations

import pytest

from app.core.security import (
    make_meta_signature,
    verify_meta_signature,
    verify_razorpay_signature,
)
from app.services.embeddings import cosine, embed
from app.services.menu_matcher import match_item
from app.services import routing
from app.whatsapp import messages

MENU = [
    {"name": "Butter Roti", "price": 20},
    {"name": "Paneer Butter Masala", "price": 120},
    {"name": "Dal Fry", "price": 90},
    {"name": "Jeera Rice", "price": 80},
]


@pytest.mark.parametrize(
    "query,expected",
    [
        ("butter rotis", "Butter Roti"),
        ("paaaneer batter musala", "Paneer Butter Masala"),
        ("jeer rice", "Jeera Rice"),
        ("dar fry", "Dal Fry"),
    ],
)
def test_menu_matcher_typos(query, expected):
    m = match_item(query, MENU)
    assert m.matched and m.menu_item["name"] == expected


@pytest.mark.parametrize("query", ["coffee", "pizza", "burger"])
def test_menu_matcher_rejects_offmenu(query):
    assert not match_item(query, MENU).matched


def test_embeddings_normalized_and_relevant():
    a = embed("3 butter rotis")
    assert abs(cosine(a, a) - 1.0) < 1e-6
    close = cosine(embed("butter roti order"), embed("i want butter rotis"))
    far = cosine(embed("butter roti"), embed("jeera rice"))
    assert close > far


def test_meta_signature_roundtrip():
    secret, body = "s3cret", b'{"hello":"world"}'
    sig = make_meta_signature(secret, body)
    assert sig.startswith("sha256=")
    assert verify_meta_signature(secret, body, sig)
    assert not verify_meta_signature(secret, body, "sha256=deadbeef")
    assert not verify_meta_signature(secret, b"tampered", sig)


def test_empty_secret_accepts():
    assert verify_meta_signature("", b"anything", None)
    assert verify_razorpay_signature("", b"anything", None)


def test_message_builder_button_limits():
    ok = messages.button_message("+91", "pick", [("a", "A"), ("b", "B")])
    assert ok["interactive"]["type"] == "button"
    with pytest.raises(messages.MessageValidationError):
        messages.button_message("+91", "x", [(str(i), str(i)) for i in range(4)])
    with pytest.raises(messages.MessageValidationError):
        messages.button_message("+91", "x", [("a", "T" * 21)])


def test_message_builder_list_limits():
    rows = [{"id": f"r{i}", "title": f"Row {i}"} for i in range(3)]
    msg = messages.list_message("+91", "body", "Open", [{"title": "S", "rows": rows}])
    assert msg["interactive"]["type"] == "list"
    too_many = [{"id": f"r{i}", "title": f"Row {i}"} for i in range(11)]
    with pytest.raises(messages.MessageValidationError):
        messages.list_message("+91", "b", "Open", [{"title": "S", "rows": too_many}])


def test_routing_greedy_and_url():
    stops = [
        routing.Stop("far", "13.10,77.70"),
        routing.Stop("near", "12.9720,77.6413"),
    ]
    d = routing.build_dispatch("12.9719,77.6412", stops)
    assert d["ordered_stops"][0].label == "near"  # nearest first
    assert d["route_url"].startswith("https://www.google.com/maps/dir/")
    assert d["total_km"] > 0
