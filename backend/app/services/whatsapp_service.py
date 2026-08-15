"""WhatsApp Cloud API outbound integration service.

Sends outbound text messages, interactive payment templates, and location prompts
directly to users via Meta's Graph API:
POST https://graph.facebook.com/v18.0/{phone_number_id}/messages
"""

from __future__ import annotations

import logging
from typing import Any
import httpx

from backend.app.core.config import settings

logger = logging.getLogger("whatsapp_service")


async def send_whatsapp_text_message(
    to_phone: str,
    text: str,
    phone_number_id: str | None = None,
    access_token: str | None = None
) -> dict[str, Any]:
    """Send an outbound text message to a user on WhatsApp via Meta Cloud API.

    Args:
        to_phone: 10-digit Indian phone number or formatted number with country code.
        text: Message body string to send.
        phone_number_id: Meta Phone Number ID (falls back to settings).
        access_token: Meta Permanent Access Token (falls back to settings).

    Returns:
        JSON response dict from Meta Graph API.
    """
    token = access_token or getattr(settings, "meta_whatsapp_token", "")
    pid = phone_number_id or getattr(settings, "meta_phone_number_id", "")

    # If credentials are not configured, log a warning (dev mode fallback)
    if not token or not pid:
        logger.warning(
            "⚠️ Meta WhatsApp credentials not set (META_WHATSAPP_TOKEN / META_PHONE_NUMBER_ID). "
            f"Message to +91{to_phone} logged locally: '{text[:80]}...'"
        )
        return {"status": "mock_logged", "to": to_phone, "text": text}

    # Standardize recipient phone with country code (91XXXXXXXXXX)
    digits = "".join(c for c in str(to_phone) if c.isdigit())
    if len(digits) == 10:
        recipient = f"91{digits}"
    elif len(digits) == 12 and digits.startswith("91"):
        recipient = digits
    else:
        recipient = f"91{digits[-10:]}"

    url = f"https://graph.facebook.com/v18.0/{pid}/messages"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": recipient,
        "type": "text",
        "text": {"body": text},
    }

    import asyncio
    import json
    import urllib.request
    import urllib.error

    def _do_post():
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers
        )
        try:
            with urllib.request.urlopen(req, timeout=10.0) as resp:
                body = resp.read().decode("utf-8")
                data = json.loads(body)
                msg_id = data.get("messages", [{}])[0].get("id", "")
                logger.info(f"🟢 WhatsApp message delivered to {recipient} (wamid: {msg_id})")
                return data
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8")
            logger.error(f"🔴 WhatsApp API HTTP {e.code} Error: {err_body}")
            return {"error": err_body, "code": e.code}
        except Exception as e:
            logger.error(f"🔴 Network error sending WhatsApp message to {recipient}: {e}")
            return {"error": str(e)}

    return await asyncio.to_thread(_do_post)
