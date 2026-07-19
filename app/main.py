"""FastAPI application factory and ASGI entrypoint (``app.main:app``)."""
from __future__ import annotations

import contextlib
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.core.config import settings
from app.core.logging import configure_logging, get_logger
from app.db.base import Base
from app.db.session import ensure_pgvector_extension, engine, SessionLocal

configure_logging()
log = get_logger("main")

BASE_DIR = Path(__file__).resolve().parent.parent


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    # Register models, ensure pgvector, create tables (demo convenience; prod
    # uses Alembic migrations), and seed baseline data if empty.
    import app.models  # noqa: F401  (register tables on Base)

    await ensure_pgvector_extension()
    if os.getenv("HOMATRI_AUTO_CREATE", "true").lower() == "true":
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    from app.seed import seed_if_empty

    async with SessionLocal() as session:
        if await seed_if_empty(session):
            log.info("baseline data seeded")
    log.info("Homaatri %s ready | wa=%s pay=%s llm=%s",
             __version__, settings.whatsapp_provider,
             settings.payment_provider, settings.llm_enabled)
    yield
    # graceful shutdown
    from app.services.llm import llm

    with contextlib.suppress(Exception):
        await llm.aclose()
    await engine.dispose()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Homaatri",
        version=__version__,
        description="WhatsApp-first hyper-local food ordering platform.",
        lifespan=lifespan,
    )

    from app.api import admin, pages, simulator, webhook

    app.include_router(webhook.router)
    app.include_router(simulator.router)
    app.include_router(admin.router)
    app.include_router(pages.router)

    static_dir = BASE_DIR / "info-website"
    if static_dir.exists():
        app.mount("/site", StaticFiles(directory=str(static_dir), html=True), name="site")

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.debug,
    )
