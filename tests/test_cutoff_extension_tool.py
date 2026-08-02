"""Integration test suite for request_cut_off_extension_tool."""

import pytest
from datetime import date, datetime
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.system import SystemAgentLog, SystemHitlSession, SystemMealWindow, SystemOutboundQueue
from app.tools.master_tools import request_cut_off_extension


@pytest.mark.asyncio
async def test_request_cut_off_extension_success(db_session: AsyncSession):
    session = db_session

    # 1. Seed OPEN SystemMealWindow
    win = SystemMealWindow(
        window_id="win_open_ext_01",
        service_date=date(2026, 8, 2),
        meal_type="LUNCH",
        cutoff_at=datetime.now(),
        status="OPEN",
    )
    session.add(win)
    await session.flush()

    # 2. Request Cutoff Extension
    hitl = await request_cut_off_extension(
        session,
        chef_phone="9876543210",
        service_date="2026-08-02",
        meal_window="LUNCH",
        extension_minutes=15,
        reason="Large thali catering prep requiring extra cooking time",
    )

    # 3. Verify HITL Session Created
    assert hitl is not None
    assert hitl.interrupt_type == "CUTOFF_EXTENSION"
    assert hitl.waiting_on_role == "ADMIN"
    assert hitl.status == "WAITING"
    assert hitl.payload["extension_minutes"] == 15

    # 4. Verify SystemAgentLog Recorded
    stmt_log = select(SystemAgentLog).where(
        SystemAgentLog.event_type == "CUTOFF_EXTENSION_REQUESTED"
    )
    audit = (await session.execute(stmt_log)).scalar_one_or_none()
    assert audit is not None
    assert audit.source_role == "CHEF"

    # 5. Verify Outbound WhatsApp Alert to Admin Enqueued
    stmt_out = select(SystemOutboundQueue).where(
        SystemOutboundQueue.recipient_role == "ADMIN"
    )
    outbound = (await session.execute(stmt_out)).scalar_one_or_none()
    assert outbound is not None
    assert "+15 mins extension" in outbound.message_text


@pytest.mark.asyncio
async def test_request_cut_off_extension_locked_window_assertion(db_session: AsyncSession):
    session = db_session

    # Seed LOCKED_PROCESSING MealWindow
    win_locked = SystemMealWindow(
        window_id="win_locked_ext_02",
        service_date=date(2026, 8, 3),
        meal_type="DINNER",
        cutoff_at=datetime.now(),
        status="LOCKED_PROCESSING",
    )
    session.add(win_locked)
    await session.flush()

    with pytest.raises(AssertionError, match="Cannot request extension for meal window with status"):
        await request_cut_off_extension(
            session,
            chef_phone="9876543210",
            service_date="2026-08-03",
            meal_window="DINNER",
            extension_minutes=10,
            reason="Late dinner prep",
        )
