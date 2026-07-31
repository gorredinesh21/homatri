"""Application configuration — 100% env-driven (12-factor, cloud-friendly).

Locally and in production, DATABASE_URL uses PostgreSQL via asyncpg/psycopg:
    DATABASE_URL=postgresql+asyncpg://dinesh:homatri_pass@localhost:5432/homatri_db
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---- Database (PostgreSQL) ----
    database_url: str = "postgresql+asyncpg://dinesh:homatri_pass@localhost:5432/homatri_db"

    # ---- LangSmith observability ----
    langchain_tracing_v2: bool = False
    langchain_api_key: str = ""
    langchain_project: str = "homatri-dev"

    # ---- GCP Vertex AI / Gemini ----
    gcp_project: str = "homatri-503308"
    gcp_location: str = "global"
    gemini_model: str = "gemini-3.6-flash"

    # ---- Business config fallbacks (authoritative values live in system_settings) ----
    default_delivery_fee: float = 30.00


settings = Settings()

