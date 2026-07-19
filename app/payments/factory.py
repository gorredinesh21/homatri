"""Payment provider selection. One switch: ``PAYMENT_PROVIDER``."""
from __future__ import annotations

from app.core.config import settings
from app.core.logging import get_logger
from app.payments.base import PaymentProvider
from app.payments.demo import DemoGateway

log = get_logger("pay.factory")

_provider: PaymentProvider | None = None


def get_payment_provider() -> PaymentProvider:
    global _provider
    if _provider is None:
        if settings.payment_provider == "razorpay":
            from app.payments.razorpay_gateway import RazorpayGateway

            _provider = RazorpayGateway()
        else:
            _provider = DemoGateway()
        log.info("Payment provider = %s", _provider.name)
    return _provider


def reset_provider() -> None:
    global _provider
    _provider = None
