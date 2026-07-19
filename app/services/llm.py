"""LLM access via LangChain AWS Bedrock (ChatBedrockConverse).

Primary model ``amazon.nova-lite-v1:0`` with ``amazon.nova-micro-v1:0`` as an
automatic failover on AWS Bedrock. Every call is bounded by a timeout and retried;
callers get a definitive result or an LLMUnavailable exception so the deterministic
offline path can take over cleanly.
"""
from __future__ import annotations

import json
from typing import Any

from langchain_aws import ChatBedrockConverse
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
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
    """Raised when no Bedrock model could satisfy the request (network/auth/models)."""


def _content_to_text(content: Any) -> str:
    """Extract ONLY the natural-language text from a Bedrock/LangChain response.

    Bedrock Converse returns ``content`` as a list of blocks that can include
    ``tool_use`` blocks. We must keep only ``text`` blocks — never stringify a
    tool_use dict, or the raw ``{'type':'tool_use',...}`` leaks to the user.
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for c in content:
            if isinstance(c, dict):
                # a text block: {"type":"text","text":"..."} or {"text":"..."}
                if isinstance(c.get("text"), str):
                    parts.append(c["text"])
                # ignore tool_use / toolUse / other block types
            elif isinstance(c, str):
                parts.append(c)
        return " ".join(p for p in parts if p).strip()
    return str(content)


def _to_langchain_messages(messages: list[dict[str, Any]]) -> list[BaseMessage]:
    """Convert dict-style OpenAI message objects to LangChain BaseMessage objects."""
    result: list[BaseMessage] = []
    for m in messages:
        role = m.get("role")
        content = m.get("content") or ""
        if role == "system":
            result.append(SystemMessage(content=content))
        elif role == "user":
            result.append(HumanMessage(content=content))
        elif role == "assistant":
            tc_list = m.get("tool_calls")
            if tc_list:
                tool_calls = []
                for tc in tc_list:
                    fn = tc.get("function", {})
                    args_str = fn.get("arguments", "{}")
                    try:
                        args = json.loads(args_str) if isinstance(args_str, str) else args_str
                    except Exception:
                        args = {}
                    tool_calls.append(
                        {
                            "name": fn.get("name", ""),
                            "args": args,
                            "id": tc.get("id", fn.get("name", "")),
                        }
                    )
                # IMPORTANT: when the assistant turn carries tool calls, send an
                # empty text content. Bedrock's Converse API (Llama 4, Qwen, etc.)
                # rejects a message that mixes text ("conversation") blocks with
                # tool-use blocks in the same turn. The filler text is unused.
                result.append(AIMessage(content="", tool_calls=tool_calls))
            else:
                result.append(AIMessage(content=content))
        elif role == "tool":
            result.append(
                ToolMessage(
                    content=content,
                    tool_call_id=m.get("tool_call_id", m.get("name", "")),
                )
            )
    return result


class LLMClient:
    def __init__(self) -> None:
        self._llm_instances: dict[str, ChatBedrockConverse] = {}

    def _get_bedrock_llm(self, model_id: str, temperature: float = 0.1) -> ChatBedrockConverse:
        cache_key = f"{model_id}:{temperature}"
        if cache_key not in self._llm_instances:
            self._llm_instances[cache_key] = ChatBedrockConverse(
                model=model_id,
                region_name=settings.aws_region,
                temperature=temperature,
            )
        return self._llm_instances[cache_key]

    async def aclose(self) -> None:
        self._llm_instances.clear()

    @property
    def enabled(self) -> bool:
        return settings.llm_enabled

    @retry(
        reraise=True,
        stop=stop_after_attempt(2),
        wait=wait_exponential(multiplier=0.5, max=4),
        retry=retry_if_exception_type(Exception),
    )
    async def _call_model(
        self,
        model_id: str,
        messages: list[dict[str, Any]],
        *,
        json_mode: bool,
        max_tokens: int,
        temperature: float,
    ) -> str:
        llm = self._get_bedrock_llm(model_id, temperature=temperature)
        lc_messages = _to_langchain_messages(messages)
        if json_mode:
            if lc_messages and isinstance(lc_messages[0], SystemMessage):
                lc_messages[0].content += "\nIMPORTANT: Reply strictly in valid JSON format."
            else:
                lc_messages.insert(0, SystemMessage(content="Reply strictly in valid JSON format."))
        
        resp = await llm.ainvoke(lc_messages, max_tokens=max_tokens)
        return clean_llm_response(_content_to_text(resp.content))

    async def chat(
        self,
        messages: list[Message],
        *,
        json_mode: bool = False,
        max_tokens: int = 512,
        temperature: float = 0.1,
    ) -> str:
        """Return assistant text, trying primary Bedrock model then fallback model."""
        if not self.enabled:
            raise LLMUnavailable("LLM disabled in configuration")

        models = [settings.bedrock_model_id, settings.bedrock_fallback_model_id]
        errors: list[str] = []
        for model in models:
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
                log.info("Bedrock LLM ok via %s (%d chars)", model, len(out))
                return out
            except Exception as e:  # noqa: BLE001
                errors.append(f"{model}: {type(e).__name__} ({e})")
                log.warning("Bedrock LLM %s failed: %s", model, e)
        raise LLMUnavailable("; ".join(errors) or "all Bedrock models failed")

    async def raw_chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict] | None = None,
        tool_choice: str = "auto",
        max_tokens: int = 400,
        temperature: float = 0.0,
    ) -> dict[str, Any]:
        """Return full assistant message dict (supports ``tool_calls``) using LangChain Bedrock."""
        if not self.enabled:
            raise LLMUnavailable("LLM disabled in configuration")

        models = [settings.bedrock_model_id, settings.bedrock_fallback_model_id]
        errors: list[str] = []
        lc_messages = _to_langchain_messages(messages)

        for model in models:
            if not model:
                continue
            try:
                llm = self._get_bedrock_llm(model, temperature=temperature)
                if tools:
                    llm_with_tools = llm.bind_tools(tools)
                    resp: AIMessage = await llm_with_tools.ainvoke(lc_messages, max_tokens=max_tokens)
                else:
                    resp = await llm.ainvoke(lc_messages, max_tokens=max_tokens)

                out_content = clean_llm_response(_content_to_text(resp.content))

                res_dict: dict[str, Any] = {
                    "role": "assistant",
                    "content": out_content,
                }
                if resp.tool_calls:
                    res_dict["tool_calls"] = [
                        {
                            "id": tc.get("id", tc["name"]),
                            "type": "function",
                            "function": {
                                "name": tc["name"],
                                "arguments": json.dumps(tc.get("args", {})),
                            },
                        }
                        for tc in resp.tool_calls
                    ]
                return res_dict
            except Exception as e:  # noqa: BLE001
                errors.append(f"{model}: {type(e).__name__} ({e})")
                log.warning("raw_chat %s failed: %s", model, e)

        raise LLMUnavailable("; ".join(errors) or "all Bedrock models failed")

    async def chat_json(
        self, messages: list[Message], *, max_tokens: int = 512
    ) -> dict[str, Any]:
        """Chat expecting a JSON object; parses defensively."""
        raw = await self.chat(
            messages, json_mode=True, max_tokens=max_tokens, temperature=0.0
        )
        return _extract_json(raw)

    async def preflight(self) -> dict[str, Any]:
        """Bedrock diagnostics report."""
        report: dict[str, Any] = {
            "aws_region": settings.aws_region,
            "bedrock_model_id": settings.bedrock_model_id,
            "llm_enabled": settings.llm_enabled,
            "chat_ok": None,
        }
        if settings.llm_enabled:
            try:
                await self.chat(
                    [{"role": "user", "content": "reply with the single word: ok"}],
                    max_tokens=5,
                )
                report["chat_ok"] = True
            except Exception as e:  # noqa: BLE001
                report["chat_ok"] = f"FAIL: {e}"
        return report


def clean_llm_response(text: str) -> str:
    """Programmatically strip <think>...</think> or <thinking>...</thinking> tags and broken markdown link placeholders."""
    if not text:
        return ""
    import re
    # 1. Strip complete <thinking>...</thinking> and <think>...</think> blocks including linebreaks
    cleaned = re.sub(r"<think(?:ing)?>.*?</think(?:ing)?>", "", text, flags=re.DOTALL | re.IGNORECASE)
    # 2. Strip any leftover orphaned tags like <thinking> or </thinking>
    cleaned = re.sub(r"</?think(?:ing)?>", "", cleaned, flags=re.IGNORECASE)
    # 3. Strip broken markdown link placeholders like [Payment Link]( or empty brackets
    cleaned = re.sub(r"\[[^\]]*\]\([^)]*", "", cleaned)
    return cleaned.strip()


def _extract_json(raw: str) -> dict[str, Any]:
    """Parse a JSON object, tolerating code fences or leading prose."""
    raw = clean_llm_response(raw)
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


# Singleton instance
llm = LLMClient()
