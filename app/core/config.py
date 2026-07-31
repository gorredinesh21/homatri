"""Application configuration — 100% env-driven (12-factor, cloud-friendly).

Locally (office laptop) DATABASE_URL defaults to in-memory SQLite, so nothing
needs to be installed. On the deploy machine, set DATABASE_URL to Postgres:

    DATABASE_URL=postgresql+psycopg://user:pass@host:5432/homatri

Everything else is read from the environment / a local .env file.
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

    # ---- Database (swap this ONE value for Postgres on the deploy machine) ----
    database_url: str = "sqlite+aiosqlite:///:memory:"

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

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")


settings = Settings()
