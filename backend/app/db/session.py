"""Async engine, session factory, and the transaction() helper (Guard 1).

Guard 1 = atomic all-or-nothing writes: every executor runs inside
`async with transaction() as session:` — commit on success, rollback on any error.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from backend.app.core.config import settings

from sqlalchemy.pool import NullPool

# PostgreSQL async engine
engine = create_async_engine(
    settings.database_url,
    pool_pre_ping=True,
    poolclass=NullPool,
)

SessionFactory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)




@asynccontextmanager
async def transaction() -> AsyncIterator[AsyncSession]:
    """Guard 1 — atomic transaction. Commits on success, rolls back on error."""
    async with SessionFactory() as session:
        async with session.begin():
            yield session


async def create_all() -> None:
    """Create every table (dev/tests on SQLite). Postgres uses Alembic migrations."""
    import backend.app.models  # noqa: F401  (registers all models on Base.metadata)
    from backend.app.db.base import Base

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def drop_all() -> None:
    from backend.app.db.base import Base

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
