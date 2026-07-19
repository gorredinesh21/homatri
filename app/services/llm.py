"""LLM access via the Hugging Face Inference Router (OpenAI-compatible).

Primary model ``meta-llama/Llama-3.1-8B-Instruct`` with ``Qwen/Qwen3-8B`` as an
automatic failover — both verified to emit clean JSON via native
``response_format={"type": "json_object"}``. Every call is bounded by a timeout
and retried; callers always get a definitive success or a raised error so the
deterministic offline path can take over.
"""
from __future__ import annotations

import json
import socket
from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger("llm")

Message = dict[str, str]


class LLMUnavailable(RuntimeError):
    """Raised when no model could satisfy the request (network/auth/models)."""


class LLMClient:
    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(30.0))
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @property
    def enabled(self) -> bool:
        return settings.llm_enabled and bool(settings.hf_token)

    @retry(
        reraise=True,
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=0.5, max=4),
        retry=retry_if_exception_type((httpx.TransportError, httpx.HTTPStatusError)),
    )
    async def _call_model(
        self,
        model: str,
        messages: list[Message],
        *,
        json_mode: bool,
        max_tokens: int,
        temperature: float,
    ) -> str:
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        resp = await self._http().post(
            settings.hf_router_url,
            headers={
                "Authorization": f"Bearer {settings.hf_token}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        # Qwen3 (a reasoning model) may wrap output in <think>...</think>; strip it.
        if "</think>" in content:
            content = content.split("</think>", 1)[1]
        return content.strip()

    async def chat(
        self,
        messages: list[Message],
        *,
        json_mode: bool = False,
        max_tokens: int = 512,
        temperature: float = 0.1,
    ) -> str:
        """Return the assistant text, trying primary then fallback model."""
        if not self.enabled:
            raise LLMUnavailable("LLM disabled or no HF token configured")

        errors: list[str] = []
        for model in (settings.llm_primary_model, settings.llm_fallback_model):
            if not model:
                continue
            try:
                out = await self._call_model(
                    model,
                    messages,
                    json_mode=json_mode,
                    max_tokens=max_tokens,
                    temperature=temperature,
                )
                log.info("LLM ok via %s (%d chars)", model, len(out))
                return out
            except httpx.HTTPStatusError as e:
                errors.append(f"{model}: HTTP {e.response.status_code}")
                log.warning("LLM %s failed: HTTP %s", model, e.response.status_code)
            except Exception as e:  # noqa: BLE001
                errors.append(f"{model}: {type(e).__name__}")
                log.warning("LLM %s failed: %s", model, e)
        raise LLMUnavailable("; ".join(errors) or "all models failed")

    async def raw_chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict] | None = None,
        tool_choice: str = "auto",
        max_tokens: int = 400,
        temperature: float = 0.0,
    ) -> dict[str, Any]:
        """Return the full assistant message dict (supports ``tool_calls``).

        Used by the agent loop. Tries primary then fallback model.
        """
        if not self.enabled:
            raise LLMUnavailable("LLM disabled or no HF token configured")
        errors: list[str] = []
        for model in (settings.llm_primary_model, settings.llm_fallback_model):
            if not model:
                continue
            payload: dict[str, Any] = {
                "model": model,
                "messages": messages,
                "max_tokens": max_tokens,
                "temperature": temperature,
            }
            if tools:
                payload["tools"] = tools
                payload["tool_choice"] = tool_choice
            try:
                resp = await self._http().post(
                    settings.hf_router_url,
                    headers={
                        "Authorization": f"Bearer {settings.hf_token}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
                resp.raise_for_status()
                return resp.json()["choices"][0]["message"]
            except Exception as e:  # noqa: BLE001
                errors.append(f"{model}: {type(e).__name__}")
                log.warning("raw_chat %s failed: %s", model, e)
        raise LLMUnavailable("; ".join(errors) or "all models failed")

    async def chat_json(
        self, messages: list[Message], *, max_tokens: int = 512
    ) -> dict[str, Any]:
        """Chat expecting a JSON object; parses defensively."""
        raw = await self.chat(
            messages, json_mode=True, max_tokens=max_tokens, temperature=0.0
        )
        return _extract_json(raw)

    async def preflight(self) -> dict[str, Any]:
        """Network/auth diagnostics, mirroring the POC's pre-flight engine."""
        report: dict[str, Any] = {
            "hf_token_present": bool(settings.hf_token),
            "llm_enabled": settings.llm_enabled,
            "dns_router": None,
            "tcp_cloudflare_dns": None,
            "chat_ok": None,
        }
        try:
            host = httpx.URL(settings.hf_router_url).host
            report["dns_router"] = socket.gethostbyname(host)
        except Exception as e:  # noqa: BLE001
            report["dns_router"] = f"FAIL: {e}"
        try:
            s = socket.create_connection(("1.1.1.1", 53), timeout=3)
            s.close()
            report["tcp_cloudflare_dns"] = "ok"
        except Exception as e:  # noqa: BLE001
            report["tcp_cloudflare_dns"] = f"FAIL: {e}"
        if report["hf_token_present"] and settings.llm_enabled:
            try:
                await self.chat(
                    [{"role": "user", "content": "reply with the single word: ok"}],
                    max_tokens=5,
                )
                report["chat_ok"] = True
            except Exception as e:  # noqa: BLE001
                report["chat_ok"] = f"FAIL: {e}"
        return report


def _extract_json(raw: str) -> dict[str, Any]:
    """Parse a JSON object, tolerating code fences or leading prose."""
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:]
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        start, end = raw.find("{"), raw.rfind("}")
        if start != -1 and end != -1 and end > start:
            return json.loads(raw[start : end + 1])
        raise


# Module-level singleton (one connection pool for the app).
llm = LLMClient()
