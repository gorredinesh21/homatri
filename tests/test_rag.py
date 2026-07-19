"""RAG memory store + retrieval on SQLite (Python cosine fallback path)."""
from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.base import Base
import app.models  # noqa: F401
from app.services import rag


@pytest.mark.asyncio
async def test_memory_roundtrip_ranks_relevant_first():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with Session() as s:
        phone = "+919876543210"
        for text in [
            "I want 3 butter rotis and jeera rice",
            "please deliver at 8:30 pm",
            "my address is Indiranagar Bengaluru",
        ]:
            await rag.add_memory(s, phone, text)
        await s.commit()

        top = await rag.query_memory(s, phone, "how many rotis did I order", top_n=1)
        assert top and "rotis" in top[0].lower()

        # isolation: another phone sees nothing
        assert await rag.query_memory(s, "+910000000000", "rotis") == []
    await engine.dispose()
