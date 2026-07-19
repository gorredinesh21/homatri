"""Real Razorpay provider (test or live keys).

Activated with ``PAYMENT_PROVIDER=razorpay`` + RAZORPAY_KEY_ID/SECRET. Creates
real Razorpay orders (amount in paise) and uses Razorpay's own utility to verify
the checkout handshake signature and webhook signatures. The order flow is
identical to the demo gateway — only this class changes.
"""
from __future__ import annotations

import razorpay
from razorpay.errors import SignatureVerificationError

from app.core.config import settings
from app.core.logging import get_logger
from app.payments.base import PaymentIntent, PaymentProvider, WebhookResult, to_minor

log = get_logger("pay.razorpay")


class RazorpayGateway(PaymentProvider):
    name = "razorpay"

    def __init__(self) -> None:
        if not (settings.razorpay_key_id and settings.razorpay_key_secret):
            raise RuntimeError(
                "PAYMENT_PROVIDER=razorpay requires RAZORPAY_KEY_ID and "
                "RAZORPAY_KEY_SECRET"
            )
        self._client = razorpay.Client(
            auth=(settings.razorpay_key_id, settings.razorpay_key_secret)
        )

    async def create_payment(
        self, *, order_code: str, amount: float, currency: str = "INR"
    ) -> PaymentIntent:
        minor = to_minor(amount)
        # razorpay SDK is sync; fine for our low call volume.
        rzp_order = self._client.order.create(
            {
                "amount": minor,
                "currency": currency,
                "receipt": order_code,
                "payment_capture": 1,
                "notes": {"order_code": order_code},
            }
        )
        return PaymentIntent(
            provider=self.name,
            provider_order_id=rzp_order["id"],
            amount=amount,
            amount_minor=minor,
            currency=currency,
            key_id=settings.razorpay_key_id,
        )

    def verify_payment_signature(
        self, provider_order_id: str, provider_payment_id: str, signature: str
    ) -> bool:
        try:
            self._client.utility.verify_payment_signature(
                {
                    "razorpay_order_id": provider_order_id,
                    "razorpay_payment_id": provider_payment_id,
                    "razorpay_signature": signature,
                }
            )
            return True
        except SignatureVerificationError:
            return False

    def verify_webhook(self, raw_body: bytes, signature: str | None) -> bool:
        if not signature or not settings.razorpay_webhook_secret:
            return False
        try:
            self._client.utility.verify_webhook_signature(
                raw_body.decode("utf-8"),
                signature,
                settings.razorpay_webhook_secret,
            )
            return True
        except SignatureVerificationError:
            return False

    def parse_webhook(self, payload: dict) -> WebhookResult:
        event = payload.get("event", "")
        entity = (
            payload.get("payload", {})
            .get("payment", {})
            .get("entity", {})
        )
        return WebhookResult(
            provider_order_id=entity.get("order_id", ""),
            provider_payment_id=entity.get("id", ""),
            paid=event in ("payment.captured", "order.paid"),
        )
