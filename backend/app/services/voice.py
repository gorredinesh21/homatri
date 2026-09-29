"""Voice-note pipeline: WhatsApp audio → text via Gemini on Vertex AI.

WhatsApp Cloud API delivers voice notes to the webhook as media references
(`messages[].type == "audio"`). We download the ogg/opus bytes from Meta's
Graph API (same token used for outbound messages), then transcribe with the
same Gemini family the chat agent already uses — ADC auth, same GCP project,
no new credentials. Gemini handles Telugu / Hindi / English code-mixing
natively, which matters for Homaatri's customers; the audio input cost is
negligible (~$0.0001 per 10-second note at flash-lite rates).
"""

from __future__ import annotations

import asyncio
import logging
from functools import lru_cache

import httpx

from backend.app.core.config import settings

logger = logging.getLogger("voice")

GRAPH_VERSION = "v21.0"  # keep in lockstep with whatsapp_service.py

# Voice notes quieter than this (dBFS RMS) are treated as accidental/silent
# sends and never reach Gemini — LLM audio transcription can hallucinate a
# fluent order over pure silence, which for a food-ordering bot is dangerous.
SILENCE_DBFS = -45.0


def _audio_rms_dbfs(data: bytes) -> float | None:
    """Decode audio → RMS level in dBFS. None if decoding is unavailable
    (never blocks transcription on a decode hiccup)."""
    try:
        import io as _io

        import numpy as _np
        import soundfile as _sf

        arr, _sr = _sf.read(_io.BytesIO(data))
        if arr.size == 0:
            return None
        rms = float(_np.sqrt(_np.mean(_np.square(arr.astype(_np.float64)))))
        return -120.0 if rms <= 0 else 20.0 * _np.log10(rms)
    except Exception:
        return None

_STT_PROMPT = (
    "You transcribe voice notes for an Indian food-ordering WhatsApp service. "
    "The customer may speak Telugu, Hindi, English, or a natural mix — transcribe "
    "EXACTLY as spoken in the original language; do NOT translate. "
    "IMPORTANT: LLM audio transcription can hallucinate fluent text over silence "
    "or noise. If the audio contains no intelligible human speech, you MUST set "
    '"speech" to false with an empty transcript. Respond with ONLY this JSON: '
    '{"speech": true|false, "transcript": "the exact words spoken"}'
)


@lru_cache(maxsize=1)
def _stt_client():
    from google import genai

    return genai.Client(vertexai=True, project=settings.gcp_project, location="us-central1")


async def download_media_bytes(media_id: str, access_token: str | None = None) -> tuple[bytes, str]:
    """Download a WhatsApp media object.

    Two steps per Meta docs: GET /{media_id} returns {url, mime_type}; the url
    (valid ~5 minutes, also Bearer-authed) returns the bytes. WhatsApp voice
    notes are opus-in-ogg, typically <1 MB — well inside Gemini's inline limit.
    """
    token = access_token or settings.meta_whatsapp_token
    if not (token and media_id):
        raise ValueError("audio media download needs META_WHATSAPP_TOKEN + a media id")
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
        r = await client.get(f"https://graph.facebook.com/{GRAPH_VERSION}/{media_id}", headers=headers)
        r.raise_for_status()
        info = r.json()
        url = info.get("url", "")
        mime = (info.get("mime_type") or "audio/ogg").split(";")[0].strip() or "audio/ogg"
        if not url:
            raise ValueError(f"media {media_id} has no download url: {info}")
        r2 = await client.get(url, headers=headers)
        r2.raise_for_status()
        return r2.content, mime


def _transcribe_sync(data: bytes, mime_type: str) -> str:
    import json as _json
    import re as _re

    from google.genai import types

    model = settings.gemini_stt_model
    dbfs = _audio_rms_dbfs(data)
    if dbfs is not None and dbfs < SILENCE_DBFS:
        logger.info(f"🎙️ audio at {dbfs:.1f} dBFS — below silence gate, skipping STT")
        return ""
    config_kwargs: dict = {
        "temperature": 0,
        "response_mime_type": "application/json",
        "response_schema": {
            "type": "object",
            "properties": {
                "speech": {"type": "boolean"},
                "transcript": {"type": "string"},
            },
            "required": ["speech", "transcript"],
        },
    }
    if "2.5" in model:  # same zero-budget trick as llm.py — keep latency interactive
        config_kwargs["thinking_config"] = types.ThinkingConfig(thinking_budget=0)
    resp = _stt_client().models.generate_content(
        model=model,
        contents=[
            types.Content(
                role="user",
                parts=[types.Part.from_bytes(data=data, mime_type=mime_type), types.Part(text=_STT_PROMPT)],
            )
        ],
        config=types.GenerateContentConfig(**config_kwargs),
    )
    raw = (resp.text or "").strip()
    try:
        start = raw.find("{")
        if start != -1:
            # raw_decode parses the FIRST balanced object and ignores the stray
            # tokens Gemini sometimes appends after it ("}\nplease" etc.)
            parsed, _ = _json.JSONDecoder().raw_decode(raw[start:])
            if parsed.get("speech") is False:
                return ""  # silence/noise — caller falls back to the "couldn't hear" message
            return str(parsed.get("transcript", "")).strip() or raw
    except (_json.JSONDecodeError, AttributeError):
        pass
    return raw  # non-JSON reply — treat the raw text as the transcript


async def transcribe_audio(data: bytes, mime_type: str) -> str:
    """Audio bytes → transcript. Blocking Vertex call is pushed to a worker thread."""
    return await asyncio.to_thread(_transcribe_sync, data, mime_type)


async def voice_note_to_text(media_id: str) -> str:
    """Full voice-note path: Meta media id → bytes → transcript ('' on silence)."""
    data, mime = await download_media_bytes(media_id)
    logger.info(f"🎙️ transcribing {len(data)} bytes of {mime}")
    return await transcribe_audio(data, mime)
