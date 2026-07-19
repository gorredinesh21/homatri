"""Async SQLAlchemy engine, session factory and FastAPI dependency."""
from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings
from app.core.logging import get_logger

log = get_logger("db")

_engine_kwargs: dict = {"echo": False}
if "sqlite" not in settings.database_url:
    # Connection pooling applies to server DBs; SQLite uses NullPool.
    _engine_kwargs.update(pool_pre_ping=True, pool_size=10, max_overflow=20)

engine: AsyncEngine = create_async_engine(settings.database_url, **_engine_kwargs)

SessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency yielding a transactional session."""
    async with SessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


async def ensure_pgvector_extension() -> None:
    """Create the pgvector extension if the backend is Postgres."""
    if "postgresql" not in settings.database_url:
        return
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    log.info("pgvector extension ensured")
