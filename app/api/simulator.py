"""Simulator ("device") endpoints used by the 3-phone browser UI.

These stand in for real phones + the Razorpay checkout. Crucially, a simulated
"send" builds a real Meta-shaped, HMAC-signed webhook body and pushes it through
the *same* ``ingest_whatsapp`` path the real Meta webhook uses — so the demo
exercises production code, not a shortcut. Payment "success" likewise posts a
signed gateway webhook.
"""
from __future__ import annotations

import json

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

from app.api.webhook import (
    _SignatureError,
    ingest_whatsapp,
    process_payment_webhook,
)
from app.core.logging import get_logger
from app.db.session import SessionLocal
from app.payments.demo import DemoGateway
from app.payments.factory import get_payment_provider
from app.services import order_lifecycle as lc
from app.whatsapp.mock import MockWhatsAppProvider

log = get_logger("api.sim")
router = APIRouter(prefix="/api/sim", tags=["simulator"])


class SendText(BaseModel):
    phone: str
    text: str
    profile_name: str = "Customer"


class Tap(BaseModel):
    phone: str
    reply_id: str
    title: str = ""


class ShareLocation(BaseModel):
    phone: str
    latitude: float
    longitude: float


def _signed(body: dict) -> tuple[bytes, str]:
    raw = json.dumps(body).encode()
    return raw, MockWhatsAppProvider.sign(raw)


@router.post("/send")
async def sim_send(payload: SendText, background_tasks: BackgroundTasks) -> dict:
    body = MockWhatsAppProvider.build_inbound_webhook(
        payload.phone, text=payload.text, profile_name=payload.profile_name
    )
    raw, sig = _signed(body)
    try:
        count = await ingest_whatsapp(raw, sig, background_tasks)
    except _SignatureError:
        raise HTTPException(403, "signature check failed")
    return {"status": "sent", "count": count}


@router.post("/tap")
async def sim_tap(payload: Tap, background_tasks: BackgroundTasks) -> dict:
    body = MockWhatsAppProvider.build_inbound_webhook(
        payload.phone, reply_id=payload.reply_id, reply_title=payload.title
    )
    raw, sig = _signed(body)
    try:
        count = await ingest_whatsapp(raw, sig, background_tasks)
    except _SignatureError:
        raise HTTPException(403, "signature check failed")
    return {"status": "tapped", "count": count}


@router.post("/location")
async def sim_location(payload: ShareLocation, background_tasks: BackgroundTasks) -> dict:
    body = MockWhatsAppProvider.build_inbound_webhook(
        payload.phone, latitude=payload.latitude, longitude=payload.longitude
    )
    raw, sig = _signed(body)
    try:
        count = await ingest_whatsapp(raw, sig, background_tasks)
    except _SignatureError:
        raise HTTPException(403, "signature check failed")
    return {"status": "location shared", "count": count}


@router.post("/pay/{code}")
async def sim_pay(code: str) -> dict:
    """Simulate a successful gateway payment by posting a signed payment webhook
    to our own ``/webhook/payment`` — same path real Razorpay would hit."""
    async with SessionLocal() as session:
        order = await lc.get_order_by_code(session, code)
        if order is None:
            raise HTTPException(404, "order not found")
        if order.payment is None:
            provider = get_payment_provider()
            intent = await provider.create_payment(
                order_code=order.code, amount=order.total, currency="INR"
            )
            from app.services.conversation import _new_payment_row
            order.payment = _new_payment_row(order, intent)
            await session.commit()
            await session.refresh(order, ["payment"])
        provider_order_id = order.payment.provider_order_id

    payment_id = "pay_" + code.replace("-", "")
    body = {
        "event": "payment.captured",
        "order_id": provider_order_id,
        "payment_id": payment_id,
        "payload": {
            "payment": {
                "entity": {"id": payment_id, "order_id": provider_order_id}
            }
        },
    }
    raw = json.dumps(body).encode()
    provider = get_payment_provider()
    if not isinstance(provider, DemoGateway):
        # A real gateway's webhook must come from the gateway (we can't forge
        # its signature). With live Razorpay, complete payment via real checkout.
        raise HTTPException(
            400, "Live Razorpay is active; complete payment via the real checkout."
        )
    # Demo gateway signs its own callback over the raw body (Razorpay scheme),
    # then we run it through the real payment-webhook handler in-process.
    from app.core.security import compute_hmac_sha256
    from app.payments.demo import DEMO_SECRET

    sig = compute_hmac_sha256(DEMO_SECRET, raw)
    status, resp = await process_payment_webhook(raw, sig)
    if status >= 400:
        raise HTTPException(status, str(resp))
    return {"status": "payment simulated", "gateway_response": resp}
