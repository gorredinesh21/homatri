"""Integration test suite for 3 Time Bracket Meal Cutoffs."""

import pytest
from datetime import datetime, date
from decimal import Decimal
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chef import ChefProfile
from app.tools.customer_tools import initialize_customer_order_tool


@pytest.mark.asyncio
async def test_initialize_customer_order_cutoff_afternoon_lunch_rejected(db_session: AsyncSession):
    session = db_session

    chef = ChefProfile(
        chef_phone="9876543210",
        kitchen_name="Indravati Tiffins",
        chef_name="Chef Sunita",
        address="Sector 4, Ghansoli",
        latitude=Decimal("19.1190086"),
        longitude=Decimal("72.9934054"),
    )
    session.add(chef)
    await session.commit()

    today_str = date.today().isoformat()
    now_hour = datetime.now().hour

    # Test initializing Lunch order
    res = await initialize_customer_order_tool.ainvoke({
        "customer_phone": "9123456789",
        "chef_phone": "9876543210",
        "service_date": today_str,
        "meal_window": "LUNCH",
    })


    if now_hour >= 12:
        assert "❌ Order Rejected" in res
        assert "Lunch cutoff (12:00 PM) has passed" in res
    else:
        assert "🛒 New Order Header Initialized" in res
