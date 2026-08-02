"""Homaatri FastAPI Core Server Application (app/main.py).

Core entrypoint for Homaatri multi-agent platform, initializing DB connection pool,
CORS middleware, and v1 API routers.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware




@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager for database connection pool management and table creation."""
    import app.models.chef  # noqa
    import app.models.customer  # noqa
    import app.models.driver  # noqa
    import app.models.system  # noqa
    from app.db.base import Base
    from app.db.session import engine

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield
    await engine.dispose()




app = FastAPI(
    title="Homaatri Multi-Agent Platform API",
    description="Multi-Agent Hyperlocal Home Food & Subscription Platform powered by GCP Vertex AI & LangGraph.",
    version="1.0.0",
    lifespan=lifespan,
)

# Configure CORS for Web Clone Frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from app.api.v1.auth import router as auth_router
from app.api.v1.chat import router as chat_router
from app.api.v1.payments import router as payments_router
from app.api.v1.whatsapp import router as whatsapp_router

from pathlib import Path
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

# Register v1 API Routers
app.include_router(auth_router, prefix="/api/v1")
app.include_router(chat_router, prefix="/api/v1")
app.include_router(payments_router, prefix="/api/v1")
app.include_router(whatsapp_router, prefix="/api/v1")

static_dir = Path(__file__).parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


@app.get("/", tags=["WhatsApp Web Clone"])
async def serve_whatsapp_web_clone():
    """Serve WhatsApp Web Clone Simulator Frontend Application."""
    index_file = static_dir / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    return FileResponse(index_file)


@app.get("/health", tags=["Health Check"])
async def health_check():
    """Health check endpoint."""
    return {
        "status": "online",
        "service": "Homaatri Multi-Agent Platform",
        "version": "1.0.0",
        "docs_url": "/docs",
    }



