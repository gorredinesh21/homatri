"""Conversation-memory RAG over pgvector.

Stores each turn's text as a 384-d embedding partitioned by phone number and
retrieves the most relevant prior turns to enrich role prompts. Uses pgvector's
``<=>`` cosine-distance operator on Postgres (indexable, fast) and a Python
fallback on SQLite for the test suite.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.entities import KnowledgeEmbedding, RelationshipMemory
from app.services.embeddings import cosine, embed

log = get_logger("rag")


async def add_memory(session: AsyncSession, phone: str, text: str) -> None:
    """Persist a conversation turn as an embedding."""
    text = (text or "").strip()
    if not text:
        return
    row = KnowledgeEmbedding(phone=phone, text=text, embedding=embed(text))
    session.add(row)
    await session.flush()


async def query_memory(
    session: AsyncSession, phone: str, text: str, top_n: int = 3
) -> list[str]:
    """Return up to ``top_n`` most-relevant prior texts for this phone."""
    text = (text or "").strip()
    if not text:
        return []
    q_vec = embed(text)
    dialect = session.bind.dialect.name if session.bind else "sqlite"

    if dialect == "postgresql":
        # cosine distance = 1 - cosine similarity; smaller is closer.
        stmt = (
            select(KnowledgeEmbedding.text)
            .where(KnowledgeEmbedding.phone == phone)
            .order_by(KnowledgeEmbedding.embedding.cosine_distance(q_vec))
            .limit(top_n)
        )
        rows = (await session.execute(stmt)).scalars().all()
        return list(rows)

    # SQLite / fallback: fetch this phone's memories and rank in Python.
    stmt = select(KnowledgeEmbedding).where(KnowledgeEmbedding.phone == phone)
    rows = (await session.execute(stmt)).scalars().all()
    scored = sorted(
        rows, key=lambda r: cosine(q_vec, list(r.embedding)), reverse=True
    )
    return [r.text for r in scored[:top_n]]


def build_context_block(memories: list[str]) -> str:
    if not memories:
        return ""
    lines = "\n".join(f"- {m}" for m in memories)
    return f"Relevant conversation history:\n{lines}"


# ── Relationship (customer↔chef↔driver) shared memory ─────────────────────────
async def remember_interaction(
    session: AsyncSession,
    *,
    customer_id: uuid.UUID,
    role: str,
    text: str,
    chef_id: uuid.UUID | None = None,
    driver_id: uuid.UUID | None = None,
    order_id: uuid.UUID | None = None,
) -> None:
    """Record one interaction into the trio's shared history."""
    text = (text or "").strip()
    if not text:
        return
    session.add(
        RelationshipMemory(
            customer_id=customer_id,
            chef_id=chef_id,
            driver_id=driver_id,
            order_id=order_id,
            role=(role or "").upper()[:16],
            text=text,
            embedding=embed(text),
        )
    )
    await session.flush()


async def recall_relationship(
    session: AsyncSession,
    *,
    customer_id: uuid.UUID,
    chef_id: uuid.UUID | None = None,
    query: str = "",
    top_n: int = 5,
) -> list[str]:
    """Return the most relevant shared-history lines for this customer↔chef
    relationship, formatted as ``[ROLE] text``. Ranked by embedding similarity
    to ``query`` when given, else most recent first."""
    stmt = select(RelationshipMemory).where(
        RelationshipMemory.customer_id == customer_id
    )
    if chef_id is not None:
        stmt = stmt.where(RelationshipMemory.chef_id == chef_id)

    if not query:
        rows = (
            await session.execute(
                stmt.order_by(RelationshipMemory.created_at.desc()).limit(top_n)
            )
        ).scalars().all()
        return [f"[{r.role}] {r.text}" for r in rows]

    q_vec = embed(query)
    dialect = session.bind.dialect.name if session.bind else "sqlite"
    if dialect == "postgresql":
        stmt = stmt.order_by(
            RelationshipMemory.embedding.cosine_distance(q_vec)
        ).limit(top_n)
        rows = (await session.execute(stmt)).scalars().all()
        return [f"[{r.role}] {r.text}" for r in rows]

    rows = (await session.execute(stmt)).scalars().all()
    ranked = sorted(rows, key=lambda r: cosine(q_vec, list(r.embedding)), reverse=True)
    return [f"[{r.role}] {r.text}" for r in ranked[:top_n]]


def build_relationship_block(memories: list[str]) -> str:
    if not memories:
        return ""
    lines = "\n".join(f"- {m}" for m in memories)
    return (
        "Shared history for this customer/chef/rider (reference it naturally "
        f"when relevant):\n{lines}"
    )
