"""LLM factory — GCP Vertex AI (Gemini) via ADC / service-account (no API key).

Model, project and location come from `settings` (env-driven). Matches the
reference setup in sample_scripts/llm.py. The client is built lazily (on first
use) so importing the agents never requires cloud credentials — auth only
happens when the model is actually invoked.
"""

from __future__ import annotations

from functools import lru_cache

from langchain_google_vertexai import ChatVertexAI

from backend.app.core.config import settings


def get_llm(model: str | None = None, temperature: float = 0.2) -> ChatVertexAI:
    """Build a ChatVertexAI client from settings (ADC auth — no API key)."""
    target_model = model or settings.gemini_model or "gemini-2.5-flash-lite"
    if "3.6" in target_model or "3.5" in target_model:
        target_model = "gemini-2.5-flash-lite"  # map non-existent aliases to active Vertex AI model ID
    return ChatVertexAI(
        model_name=target_model,
        project=settings.gcp_project,
        location="us-central1",
        temperature=temperature,
        max_retries=0,
    )



@lru_cache(maxsize=1)
def shared_llm() -> ChatVertexAI:
    """One shared, lazily-built base client reused by all four agents.

    Tools are NOT bound here — each agent binds its own tool subset later,
    per flow, via `shared_llm().bind_tools(...)`.
    """
    return get_llm()
