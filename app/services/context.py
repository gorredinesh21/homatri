"""The Context Assembler — builds the one structured context block that every
agent prompt uses, so the model never "forgets" the order, the recent thread,
or the rules.

Assembled each turn from:
  • POLICY        — stage-specific rules (app.services.policy)
  • ORDER STATE   — the authoritative order snapshot (lifecycle service)
  • OPEN ITEMS    — unresolved threads (pending change requests)
  • RECENT CHAT   — the last N turns verbatim (from RelationshipMemory)
  • REMEMBER      — long-term trio facts (pgvector recall)
  • ROLLING SUMMARY — compacted older turns for very long conversations
  • MENU          — the chef's live menu
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.entities import ConversationState, Order, RelationshipMemory
from app.models.enums import ChangeStatus
from app.services import policy, rag
from app.services.llm import LLMUnavailable, llm

log = get_logger("context")

_RECENT_TURNS = 10          # verbatim transcript window
_COMPACT_EVERY = 6          # compact the rolling summary every N turns
_COMPACT_AFTER = 12         # ...once the conversation is at least this long


async def _recent_transcript(session: AsyncSession, order: Order, limit: int) -> list[str]:
    rows = (
        await session.execute(
            select(RelationshipMemory)
            .where(RelationshipMemory.order_id == order.id)
            .order_by(RelationshipMemory.created_at.desc())
            .limit(limit)
        )
    ).scalars().all()
    return [f"[{r.role}] {r.text}" for r in reversed(rows)]


def _open_threads(order: Order) -> list[str]:
    out = []
    for c in order.change_requests:
        if c.status == ChangeStatus.PENDING:
            out.append(f"{c.description} (awaiting approval)")
    return out


async def get_state(session: AsyncSession, order: Order) -> ConversationState:
    st = (
        await session.execute(
            select(ConversationState).where(ConversationState.order_id == order.id)
        )
    ).scalar_one_or_none()
    if st is None:
        st = ConversationState(order_id=order.id, summary="", open_threads=[], turn_count=0)
        session.add(st)
        await session.flush()
    return st


async def build_agent_context(
    session: AsyncSession,
    *,
    persona: str,
    order: Order | None,
    menu,
    query: str,
    customer_name: str = "",
) -> str:
    """Return the full structured context block for an agent's system prompt."""
    from app.services.order_lifecycle import build_active_order_context
    from app.services.conversation import _menu_text  # local import avoids cycle

    parts: list[str] = [persona.strip()]

    # POLICY (stage-specific rules)
    parts.append("=== POLICY (what you may do now) ===\n" + policy.policy_text(order))

    # ORDER STATE
    parts.append("=== ORDER STATE ===\n" + build_active_order_context(order))

    if order is not None:
        # OPEN THREADS
        threads = _open_threads(order)
        if threads:
            parts.append("=== OPEN ITEMS (unresolved) ===\n" + "\n".join(f"- {t}" for t in threads))

        # ROLLING SUMMARY (older context, if compacted)
        st = await get_state(session, order)
        if st.summary:
            parts.append("=== EARLIER IN THIS CONVERSATION ===\n" + st.summary)

        # RECENT CHAT (verbatim)
        transcript = await _recent_transcript(session, order, _RECENT_TURNS)
        if transcript:
            parts.append("=== RECENT CONVERSATION (most recent last) ===\n" + "\n".join(transcript))

        # LONG-TERM TRIO MEMORY
        mems = await rag.recall_relationship(
            session, customer_id=order.customer_id, chef_id=order.chef_id,
            query=query, top_n=4,
        )
        block = rag.build_relationship_block(mems)
        if block:
            parts.append("=== REMEMBER (long-term facts) ===\n" + block)

    if customer_name and customer_name not in ("", "Customer"):
        parts.append(f"The customer's name is {customer_name}.")

    # MENU
    if menu:
        parts.append("=== MENU ===\n" + _menu_text(menu))

    return "\n\n".join(parts)


async def bump_and_maybe_compact(session: AsyncSession, order: Order | None) -> None:
    """Increment the turn counter and, on long conversations, compact older
    turns into ``ConversationState.summary`` so the prompt stays bounded."""
    if order is None:
        return
    st = await get_state(session, order)
    st.turn_count += 1
    if not (llm.enabled and st.turn_count >= _COMPACT_AFTER and st.turn_count % _COMPACT_EVERY == 0):
        return
    # Summarise everything except the most recent window.
    rows = (
        await session.execute(
            select(RelationshipMemory)
            .where(RelationshipMemory.order_id == order.id)
            .order_by(RelationshipMemory.created_at.asc())
        )
    ).scalars().all()
    older = rows[:-_RECENT_TURNS] if len(rows) > _RECENT_TURNS else []
    if not older:
        return
    convo = "\n".join(f"[{r.role}] {r.text}" for r in older)
    try:
        summary = await llm.chat(
            [
                {"role": "system", "content": "Summarise this food-order conversation into 4-6 terse bullet points capturing decisions, preferences, and unresolved items. No preamble."},
                {"role": "user", "content": convo[:6000]},
            ],
            max_tokens=250,
        )
        st.summary = summary.strip()
        log.info("compacted conversation summary for order %s (%d turns)", order.code, st.turn_count)
    except (LLMUnavailable, Exception) as e:  # noqa: BLE001
        log.warning("summary compaction skipped: %s", e)
