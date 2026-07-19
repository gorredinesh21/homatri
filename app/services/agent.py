"""Generic tool-calling agent loop (provider-agnostic, domain-agnostic).

Given a system prompt, the user's message, and a set of ``Tool``s, it runs the
standard agent loop against the LLM: the model decides which tool(s) to call
with what arguments, we execute the handlers, feed results back, and repeat
until the model returns a plain-text answer (or we hit ``max_steps``).

Tools carry ``is_action``: action tools mutate state / notify other parties and
own their side effects; the agent's final natural-language text is what the
*calling* code sends back to the acting user. This keeps authoritative outputs
(payment links, buttons) exact while letting the model phrase replies naturally.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from app.core.logging import get_logger
from app.services.llm import LLMUnavailable, llm

log = get_logger("agent")

ToolHandler = Callable[..., Awaitable[str]]


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict  # JSON schema for the arguments object
    handler: ToolHandler
    is_action: bool = True

    def spec(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


@dataclass
class AgentResult:
    text: str = ""                     # final natural-language reply
    actions: list[str] = field(default_factory=list)  # names of action tools run
    tool_results: list[str] = field(default_factory=list)

    @property
    def acted(self) -> bool:
        return bool(self.actions)


async def run_agent(
    system: str,
    user_message: str,
    tools: list[Tool],
    *,
    max_steps: int = 6,
) -> AgentResult:
    """Run the tool-calling loop. Raises LLMUnavailable if the LLM can't be used.

    Hardened against small-model tool-loop failure modes:
      • duplicate identical tool calls in a turn are NOT re-executed (prevents
        e.g. sending the same WhatsApp message 3x) — the model is nudged to finish;
      • on the final step the model is called WITHOUT tools, forcing a real text
        reply instead of "hit max_steps".
    """
    tool_map = {t.name: t for t in tools}
    specs = [t.spec() for t in tools]
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system},
        {"role": "user", "content": user_message},
    ]
    result = AgentResult()
    executed: set[tuple[str, str]] = set()

    for step in range(max_steps):
        final_step = step == max_steps - 1
        try:
            # Always keep tools bound (dropping them when the history already
            # holds tool blocks makes Bedrock reject the request). On the final
            # step we simply stop executing tools and force a text answer below.
            msg = await llm.raw_chat(messages, tools=specs, tool_choice="auto")
        except LLMUnavailable:
            if step == 0 and not result.tool_results:
                raise  # nothing happened yet -> clean deterministic fallback
            log.warning("LLM dropped mid-agent-run; returning after %d step(s)", step)
            result.text = _clean(result.tool_results[-1]) if result.tool_results else (
                "Done." if result.acted else "Okay."
            )
            return result

        tool_calls = msg.get("tool_calls") or []
        if final_step or not tool_calls:
            result.text = _clean(msg.get("content") or "")
            if not result.text and result.tool_results:
                result.text = _clean(result.tool_results[-1])
            return result

        messages.append(
            {"role": "assistant", "content": msg.get("content") or "", "tool_calls": tool_calls}
        )
        for tc in tool_calls:
            fn = tc.get("function", {})
            name = fn.get("name", "")
            raw_args = fn.get("arguments") or "{}"
            try:
                args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
            except json.JSONDecodeError:
                args = {}
            key = (name, json.dumps(args, sort_keys=True, default=str))
            tool = tool_map.get(name)
            if tool is None:
                out = f"Error: unknown tool '{name}'."
            elif key in executed:
                # Identical call already run this turn — don't repeat the side effect.
                out = (f"You already called '{name}' with the same arguments. Do NOT "
                       "call it again — give the user your final reply now.")
            else:
                executed.add(key)
                try:
                    out = await tool.handler(**args)
                    if tool.is_action:
                        result.actions.append(name)
                    # Only *real* successful outputs may become the fallback reply
                    # (never an internal nudge or error string).
                    result.tool_results.append(out)
                except Exception as e:  # noqa: BLE001
                    log.exception("tool %s failed", name)
                    out = f"Error running {name}: {e}"
            log.info("agent tool %s -> %s", name, str(out)[:80])
            messages.append(
                {"role": "tool", "tool_call_id": tc.get("id", name), "name": name, "content": out}
            )

    result.text = result.text or "Okay, all set!"
    return result


def _clean(text: str) -> str:
    text = str(text or "")
    if "</think>" in text:
        text = text.split("</think>", 1)[1]
    return text.strip()
