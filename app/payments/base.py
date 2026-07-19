"""Payment provider interface.

Both the demo gateway and real Razorpay implement this, so ``PAYMENT_PROVIDER``
flips the backend with no change to the order flow. Amounts are handled in both
rupees (float, for display/DB) and minor units (paise, int) since real gateways
transact in minor units.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass


@dataclass
class PaymentIntent:
    provider: str
    provider_order_id: str
    amount: float            # rupees
    amount_minor: int        # paise
    currency: str = "INR"
    key_id: str = ""         # publishable key for client checkout (Razorpay)
    checkout_url: str = ""   # hosted page (demo gateway)


@dataclass
class WebhookResult:
    provider_order_id: str
    provider_payment_id: str
    paid: bool


def to_minor(amount: float) -> int:
    return int(round(amount * 100))


class PaymentProvider(abc.ABC):
    name: str = "base"

    @abc.abstractmethod
    async def create_payment(
        self, *, order_code: str, amount: float, currency: str = "INR"
    ) -> PaymentIntent:
        ...

    @abc.abstractmethod
    def verify_payment_signature(
        self, provider_order_id: str, provider_payment_id: str, signature: str
    ) -> bool:
        """Client-side handshake verification (checkout success callback)."""

    @abc.abstractmethod
    def verify_webhook(self, raw_body: bytes, signature: str | None) -> bool:
        """Server-to-server webhook signature verification."""

    @abc.abstractmethod
    def parse_webhook(self, payload: dict) -> WebhookResult:
        ...
