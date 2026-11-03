"""Dish-image storage: local uploads dir by default, GCS bucket when configured.

Follows the existing reels-upload pattern (backend/uploads served at /media) so
local dev and the current deployment keep working unchanged. When
MEDIA_GCS_BUCKET is set (e.g. homatri-media-prod-503308) files go to Cloud
Storage instead so images survive container restarts; auth is Application
Default Credentials via google-auth (already a dependency) — no extra client lib.
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path

from backend.app.core.config import settings

logger = logging.getLogger("homatri_media")

ALLOWED_IMAGE_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/heic": ".heic",
    "image/jpg": ".jpg",
}
MAX_IMAGE_BYTES = 5 * 1024 * 1024  # 5 MB


def validate_image(data: bytes, content_type: str | None, filename: str | None) -> str:
    """Return the normalized file extension for an upload, or raise ValueError."""
    if not data:
        raise ValueError("Empty image file")
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError("Max image size is 5 MB")
    ext: str | None = None
    ct = (content_type or "").split(";")[0].strip().lower()
    if ct in ALLOWED_IMAGE_TYPES:
        ext = ALLOWED_IMAGE_TYPES[ct]
    if ext is None and filename:
        suffix = Path(filename).suffix.lower()
        if suffix in {".jpg", ".jpeg", ".png", ".webp", ".heic"}:
            ext = ".jpg" if suffix == ".jpeg" else suffix
    if ext is None:
        raise ValueError("Upload a JPG, PNG, WEBP or HEIC image")
    return ext


def _save_local(chef_phone: str, ext: str, data: bytes) -> str:
    folder = Path(settings.uploads_dir) / "dishes" / chef_phone
    folder.mkdir(parents=True, exist_ok=True)
    name = f"{uuid.uuid4().hex}{ext}"
    (folder / name).write_bytes(data)
    return f"/media/dishes/{chef_phone}/{name}"


async def _save_gcs(chef_phone: str, ext: str, data: bytes) -> str:
    bucket = settings.media_gcs_bucket
    object_name = f"dishes/{chef_phone}/{uuid.uuid4().hex}{ext}"
    # Import lazily: google-auth is a hard dep, httpx powers the WhatsApp calls.
    import google.auth
    import google.auth.transport.requests
    import httpx

    creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/devstorage.read_write"])
    if not creds.valid:
        creds.refresh(google.auth.transport.requests.Request())
    url = (
        f"https://storage.googleapis.com/upload/storage/v1/b/{bucket}/o"
        f"?uploadType=media&name={object_name}"
    )
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            url,
            content=data,
            headers={
                "Authorization": f"Bearer {creds.token}",
                "Content-Type": "application/octet-stream",
            },
        )
    if resp.status_code not in (200, 201):
        raise RuntimeError(f"GCS upload failed ({resp.status_code}): {resp.text[:200]}")
    return f"https://storage.googleapis.com/{bucket}/{object_name}"


async def save_dish_image(chef_phone: str, data: bytes, content_type: str | None, filename: str | None) -> str:
    """Validate and persist a dish photo; returns the public URL of the stored image."""
    ext = validate_image(data, content_type, filename)
    if settings.media_gcs_bucket:
        try:
            return await _save_gcs(chef_phone, ext, data)
        except Exception:
            # A misconfigured bucket must not take dish saving down — fall back to
            # local storage and log loudly so ops sees it.
            logger.exception("GCS dish-image upload failed; falling back to local uploads dir")
    return _save_local(chef_phone, ext, data)
