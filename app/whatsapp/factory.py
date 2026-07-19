"""Provider selection. One switch: ``WHATSAPP_PROVIDER``."""
from __future__ import annotations

from app.core.config import settings
from app.core.logging import get_logger
from app.whatsapp.base import WhatsAppProvider
from app.whatsapp.mock import MockWhatsAppProvider

log = get_logger("wa.factory")

_provider: WhatsAppProvider | None = None


def get_whatsapp_provider() -> WhatsAppProvider:
    global _provider
    if _provider is None:
        if settings.whatsapp_provider == "meta":
            from app.whatsapp.meta import MetaCloudProvider

            _provider = MetaCloudProvider()
        else:
            _provider = MockWhatsAppProvider()
        log.info("WhatsApp provider = %s", _provider.name)
    return _provider


def reset_provider() -> None:
    """Test hook to force re-selection."""
    global _provider
    _provider = None
