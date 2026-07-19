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
    max_steps: int = 5,
) -> AgentResult:
    """Run the tool-calling loop. Raises LLMUnavailable if the LLM can't be used."""
    tool_map = {t.name: t for t in tools}
    specs = [t.spec() for t in tools]
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system},
        {"role": "user", "content": user_message},
    ]
    result = AgentResult()

    for step in range(max_steps):
        try:
            msg = await llm.raw_chat(messages, tools=specs, tool_choice="auto")
        except LLMUnavailable:
            # First call failed and nothing has happened yet -> let the caller
            # fall back to the deterministic path cleanly (no partial state).
            if step == 0 and not result.tool_results:
                raise
            # We already executed tool(s) this run; DO NOT let the caller
            # re-process the message (which would double-act). Return with a
            # reply derived from the last tool result.
            log.warning("LLM dropped mid-agent-run; returning after %d step(s)", step)
            result.text = result.tool_results[-1] if result.tool_results else (
                "Done." if result.acted else "Okay."
            )
            return result
        tool_calls = msg.get("tool_calls") or []

        if not tool_calls:
            content = msg.get("content") or ""
            if "</think>" in content:
                content = content.split("</think>", 1)[1].strip()
            result.text = content
            return result

        # Record the assistant's tool-call turn, then execute each call.
        messages.append(
            {"role": "assistant", "content": msg.get("content") or "", "tool_calls": tool_calls}
        )
        for tc in tool_calls:
            fn = tc.get("function", {})
            name = fn.get("name", "")
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            tool = tool_map.get(name)
            if tool is None:
                out = f"Error: unknown tool '{name}'."
            else:
                try:
                    out = await tool.handler(**args)
                    if tool.is_action:
                        result.actions.append(name)
                except Exception as e:  # noqa: BLE001
                    log.exception("tool %s failed", name)
                    out = f"Error running {name}: {e}"
            result.tool_results.append(out)
            log.info("agent tool %s -> %s", name, out[:80])
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.get("id", name),
                    "name": name,
                    "content": out,
                }
            )

    log.warning("agent hit max_steps without a final message")
    result.text = result.text or "Sorry, I couldn't finish that — please try again."
    return result
