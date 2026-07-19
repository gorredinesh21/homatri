"""Central application configuration.

All runtime behaviour is driven by environment variables (see ``.env.example``).
Provider selection (``WHATSAPP_PROVIDER`` / ``PAYMENT_PROVIDER``) is what lets us
run a fully self-contained demo today and flip to real Meta + Razorpay later
without touching application code.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ── App ──
    app_env: Literal["development", "production"] = "development"
    debug: bool = True
    public_base_url: str = "http://localhost:8000"

    # ── Database ──
    database_url: str = (
        "postgresql+asyncpg://homatri:homatri@localhost:5432/homatri"
    )

    # ── LLM & AWS Bedrock ──
    aws_region: str = "us-east-1"
    bedrock_model_id: str = "amazon.nova-lite-v1:0"
    bedrock_fallback_model_id: str = "amazon.nova-micro-v1:0"
    bedrock_embedding_model_id: str = "amazon.titan-embed-text-v2:0"
    hf_router_url: str = "https://router.huggingface.co/v1/chat/completions"
    hf_token: str = ""
    hf_token_part1: str = ""
    hf_token_part2: str = ""
    llm_primary_model: str = "amazon.nova-lite-v1:0"
    llm_fallback_model: str = "amazon.nova-micro-v1:0"
    llm_enabled: bool = True

    # ── WhatsApp ──
    whatsapp_provider: Literal["mock", "meta"] = "mock"
    whatsapp_verify_token: str = "HOMAATRI_VERIFY_TOKEN_2026"
    meta_app_secret: str = ""
    meta_access_token: str = ""
    meta_phone_number_id: str = ""
    meta_graph_version: str = "v25.0"

    # ── Payments ──
    payment_provider: Literal["demo", "razorpay"] = "demo"
    razorpay_key_id: str = ""
    razorpay_key_secret: str = ""
    razorpay_webhook_secret: str = ""

    # ── Simulation knobs ──
    simulate_latency: float = 0.4
    whatsapp_timeout_limit: float = 3.0

    @model_validator(mode="after")
    def _assemble_hf_token(self) -> "Settings":
        # Support the legacy split-token POC style (HF_TOKEN_PART1 + PART2).
        if not self.hf_token and (self.hf_token_part1 or self.hf_token_part2):
            object.__setattr__(
                self, "hf_token", self.hf_token_part1 + self.hf_token_part2
            )
        return self

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def sync_database_url(self) -> str:
        """Alembic / sync tools want the psycopg or plain driver form."""
        return self.database_url.replace("+asyncpg", "")


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
