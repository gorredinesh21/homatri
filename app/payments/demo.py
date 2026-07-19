"""Self-contained demo payment gateway.

Behaves like a real gateway for demos: mints an order id, exposes a hosted
checkout page, and signs its success webhook with HMAC-SHA256 (same scheme as
Razorpay) so our webhook verification path runs for real. No external calls, no
keys required — safe to show live.
"""
from __future__ import annotations

import uuid

from app.core.config import settings
from app.core.security import compute_hmac_sha256
from app.payments.base import PaymentIntent, PaymentProvider, WebhookResult, to_minor

# A fixed secret used only by the demo gateway to sign its own callbacks.
DEMO_SECRET = settings.razorpay_webhook_secret or "HOMAATRI_DEMO_SECRET"


class DemoGateway(PaymentProvider):
    name = "demo"

    async def create_payment(
        self, *, order_code: str, amount: float, currency: str = "INR"
    ) -> PaymentIntent:
        provider_order_id = "demo_order_" + uuid.uuid4().hex[:16]
        return PaymentIntent(
            provider=self.name,
            provider_order_id=provider_order_id,
            amount=amount,
            amount_minor=to_minor(amount),
            currency=currency,
            checkout_url=f"{settings.public_base_url}/pay/{order_code}",
        )

    def sign(self, order_id: str, payment_id: str) -> str:
        return compute_hmac_sha256(DEMO_SECRET, f"{order_id}|{payment_id}".encode())

    def verify_payment_signature(
        self, provider_order_id: str, provider_payment_id: str, signature: str
    ) -> bool:
        expected = self.sign(provider_order_id, provider_payment_id)
        return expected == signature

    def verify_webhook(self, raw_body: bytes, signature: str | None) -> bool:
        if not signature:
            return False
        return compute_hmac_sha256(DEMO_SECRET, raw_body) == signature.strip()

    def parse_webhook(self, payload: dict) -> WebhookResult:
        return WebhookResult(
            provider_order_id=payload.get("order_id", ""),
            provider_payment_id=payload.get("payment_id", ""),
            paid=payload.get("event") == "payment.captured",
        )
