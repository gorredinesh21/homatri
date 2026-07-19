"""HMAC signing helpers.

Meta signs every webhook body with ``X-Hub-Signature-256: sha256=<hex>`` using
the App Secret over the *raw* request bytes. Our mock provider produces the same
header so our verification path is exercised end-to-end and stays correct when
we switch to the real Meta Cloud API. Razorpay uses the same HMAC-SHA256 scheme
for its payment webhooks.
"""
from __future__ import annotations

import hashlib
import hmac


def compute_hmac_sha256(secret: str, payload: bytes) -> str:
    """Return the hex digest of HMAC-SHA256(secret, payload)."""
    return hmac.new(
        secret.encode("utf-8"), payload, hashlib.sha256
    ).hexdigest()


def make_meta_signature(secret: str, payload: bytes) -> str:
    """Format used by Meta's X-Hub-Signature-256 header."""
    return "sha256=" + compute_hmac_sha256(secret, payload)


def verify_meta_signature(secret: str, payload: bytes, header: str | None) -> bool:
    """Constant-time verify of Meta's X-Hub-Signature-256 header.

    An empty secret means signature enforcement is disabled (mock/demo mode),
    so we accept. This mirrors how you'd disable verification in a sandbox.
    """
    if not secret:
        return True
    if not header:
        return False
    expected = make_meta_signature(secret, payload)
    return hmac.compare_digest(expected, header.strip())


def verify_razorpay_signature(secret: str, payload: bytes, header: str | None) -> bool:
    """Razorpay webhook signature: HMAC-SHA256 hex (no 'sha256=' prefix)."""
    if not secret:
        return True
    if not header:
        return False
    expected = compute_hmac_sha256(secret, payload)
    return hmac.compare_digest(expected, header.strip())
