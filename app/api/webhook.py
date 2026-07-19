"""Production-facing webhooks: WhatsApp inbound + payment gateway.

The WhatsApp POST verifies the ``X-Hub-Signature-256`` over the *raw* body, then
parses and enqueues each message to a background task, returning ``200`` well
within Meta's 3-second SLA. The GET implements the verification handshake.
"""
from __future__ import annotations

import json

from fastapi import APIRouter, BackgroundTasks, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse

from app.core.config import settings
from app.core.logging import get_logger
from app.core.security import verify_meta_signature
from app.db.session import SessionLocal
from app.payments.factory import get_payment_provider
from app.services import order_lifecycle as lc
from app.services.conversation import on_payment_success
from app.whatsapp.factory import get_whatsapp_provider
from app.whatsapp.inbound import parse_inbound
from app.workers.dispatcher import handle_inbound_message

log = get_logger("api.webhook")
router = APIRouter(tags=["webhooks"])


@router.get("/webhook/whatsapp")
async def verify_whatsapp(request: Request) -> Response:
    params = request.query_params
    if (
        params.get("hub.mode") == "subscribe"
        and params.get("hub.verify_token") == settings.whatsapp_verify_token
    ):
        return PlainTextResponse(params.get("hub.challenge", ""))
    return PlainTextResponse("verification failed", status_code=403)


async def ingest_whatsapp(
    raw: bytes, signature: str | None, background_tasks: BackgroundTasks
) -> int:
    """Verify signature, parse, enqueue. Shared by the webhook and simulator.

    Returns the number of messages enqueued. Raises on bad signature/JSON.
    """
    if not verify_meta_signature(settings.meta_app_secret, raw, signature):
        raise _SignatureError()
    payload = json.loads(raw)
    messages = parse_inbound(payload)
    for msg in messages:
        # Offload heavy work; ACK fast to satisfy the 3s SLA.
        background_tasks.add_task(handle_inbound_message, msg)
    return len(messages)


class _SignatureError(Exception):
    pass


@router.post("/webhook/whatsapp")
async def receive_whatsapp(
    request: Request, background_tasks: BackgroundTasks
) -> JSONResponse:
    raw = await request.body()
    sig = request.headers.get("X-Hub-Signature-256")
    try:
        count = await ingest_whatsapp(raw, sig, background_tasks)
    except _SignatureError:
        log.warning("rejected webhook: bad signature")
        return JSONResponse({"error": "invalid signature"}, status_code=403)
    except json.JSONDecodeError:
        return JSONResponse({"error": "bad json"}, status_code=400)
    return JSONResponse({"status": "received", "count": count})


async def process_payment_webhook(raw: bytes, signature: str | None) -> tuple[int, dict]:
    """Verify + apply a payment webhook. Shared by the endpoint and simulator.

    Returns (http_status, body). In-process so it is unit-testable without a
    live socket.
    """
    pay = get_payment_provider()
    if not pay.verify_webhook(raw, signature):
        log.warning("rejected payment webhook: bad signature")
        return 403, {"error": "invalid signature"}

    result = pay.parse_webhook(json.loads(raw))
    if not result.paid:
        return 200, {"status": "ignored"}

    wa = get_whatsapp_provider()
    async with SessionLocal() as session:
        order = await _find_order_by_provider_order(session, result.provider_order_id)
        if order is None:
            return 404, {"status": "order not found"}
        if order.payment:
            order.payment.provider_payment_id = result.provider_payment_id
        await on_payment_success(session, wa, order)
        await session.commit()
        code = order.code
    return 200, {"status": "processed", "order": code}


@router.post("/webhook/payment")
async def receive_payment(request: Request) -> JSONResponse:
    raw = await request.body()
    sig = request.headers.get("X-Razorpay-Signature") or request.headers.get(
        "X-Homatri-Signature"
    )
    status, body = await process_payment_webhook(raw, sig)
    return JSONResponse(body, status_code=status)


async def _find_order_by_provider_order(session, provider_order_id: str):
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from app.models.entities import Order, Payment

    stmt = (
        select(Order)
        .join(Payment, Payment.order_id == Order.id)
        .where(Payment.provider_order_id == provider_order_id)
        .options(
            selectinload(Order.items),
            selectinload(Order.payment),
            selectinload(Order.delivery),
            selectinload(Order.change_requests),
        )
    )
    return (await session.execute(stmt)).scalars().first()
