"""Application configuration — 100% env-driven (12-factor, cloud-friendly).

Locally and in production, DATABASE_URL uses PostgreSQL via asyncpg/psycopg:
    DATABASE_URL=postgresql+asyncpg://dinesh:homatri_pass@localhost:5432/homatri_db
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", ".env.local", ".env.dev"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---- Environment Lifecycle ----
    env_state: str = "development"  # Options: 'development', 'staging', 'production'

    # ---- Database (PostgreSQL) ----
    database_url: str = "postgresql+asyncpg://dinesh:homatri_pass@localhost:5432/homatri_db"


    # ---- LangSmith observability ----
    langchain_tracing_v2: bool = False
    langchain_api_key: str = ""
    langchain_project: str = "homatri-dev"

    # ---- GCP Vertex AI / Gemini ----
    gcp_project: str = "homatri-503308"
    gcp_location: str = "global"
    gemini_model: str = "gemini-2.5-flash-lite"

    # ---- Business config fallbacks (authoritative values live in system_settings) ----
    default_delivery_fee: float = 30.00
    ops_phone: str = ""
    uploads_dir: str = "backend/uploads"

    # ---- Razorpay Payment Gateway ----
    razorpay_key_id: str = "rzp_test_mock_12345"
    razorpay_key_secret: str = "mock_secret_67890"
    razorpay_webhook_secret: str = "mock_webhook_secret_9999"
    razorpay_mock_mode: bool = True
    # When True, online orders charge a fixed token amount (e.g. ₹1) regardless of
    # cart total — used while testing payments. Set False to charge real totals.
    payment_force_token_amount: bool = True
    payment_token_amount_rupees: float = 1.00

    # ---- Google Maps (Routes API) ----
    # Empty -> maps_service runs in MOCK mode (nearest-neighbour). Set a real key
    # (GCP: enable Routes API + billing) to switch to live route optimization.
    google_maps_api_key: str = ""

    # ---- Meta WhatsApp Cloud API ----
    meta_phone_number_id: str = ""
    meta_whatsapp_token: str = ""

    # ---- Auth / MSG91 OTP Widget / Google OAuth ----
    jwt_secret: str = "homatri-dev-jwt-secret-change-in-production"
    jwt_access_ttl_seconds: int = 3600
    jwt_refresh_ttl_seconds: int = 60 * 60 * 24 * 30
    msg91_widget_id: str = "3668776a6f65313935373431"
    msg91_widget_token: str = "563549TIHmC7w7bhL6a8acd1aP1"
    google_oauth_client_id: str = (
        "195132182954-ooatsl0i96re4hcd8fvm95s4g2g6lf8d.apps.googleusercontent.com"
    )
    google_oauth_client_secret: str = ""



settings = Settings()

