"""Relationship (customer↔chef↔driver) shared-memory: store + recall + isolation."""
from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.base import Base
import app.models  # noqa: F401
from app.models.entities import Chef, Driver, MenuItem, Order, User
from app.models.enums import OrderStatus, UserRole
from app.services import rag


async def _seed(s: AsyncSession):
    cust = User(phone="+9111", name="Asha", role=UserRole.CUSTOMER)
    cust2 = User(phone="+9122", name="Ravi", role=UserRole.CUSTOMER)
    chefu = User(phone="+9133", name="Kiran", role=UserRole.CHEF)
    drvu = User(phone="+9144", name="Suresh", role=UserRole.DRIVER)
    s.add_all([cust, cust2, chefu, drvu]); await s.flush()
    chef = Chef(user_id=chefu.id, kitchen_name="K", kitchen_address="A", gps_coordinates="1,2")
    drv = Driver(user_id=drvu.id, vehicle_type="bike", license_plate="X", current_gps_coordinates="1,2")
    s.add_all([chef, drv]); await s.flush()
    order = Order(code="HM-T1", customer_id=cust.id, chef_id=chef.id, status=OrderStatus.CONFIRMED)
    s.add(order); await s.flush()
    return cust, cust2, chef, drv, order


@pytest.mark.asyncio
async def test_trio_shared_memory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with Session() as s:
        cust, cust2, chef, drv, order = await _seed(s)

        # Interactions across all three roles on the same order/trio.
        await rag.remember_interaction(s, customer_id=cust.id, chef_id=chef.id,
                                       order_id=order.id, role="CUSTOMER",
                                       text="please make the paneer less spicy")
        await rag.remember_interaction(s, customer_id=cust.id, chef_id=chef.id,
                                       driver_id=drv.id, order_id=order.id, role="DRIVER",
                                       text="gate code for the building is 4321")
        # A different customer with the same chef — must NOT leak.
        await rag.remember_interaction(s, customer_id=cust2.id, chef_id=chef.id,
                                       role="CUSTOMER", text="extra spicy always")
        await s.commit()

        # Chef's assistant recalls the shared note about THIS customer.
        hits = await rag.recall_relationship(s, customer_id=cust.id, chef_id=chef.id,
                                             query="how spicy should the paneer be?")
        joined = " ".join(hits).lower()
        assert "less spicy" in joined
        assert "extra spicy" not in joined          # isolation from other customer

        # Driver's assistant can recall the gate code from shared history.
        hits2 = await rag.recall_relationship(s, customer_id=cust.id, chef_id=chef.id,
                                              query="what's the building access code?")
        assert any("4321" in h for h in hits2)

        # Unrelated customer sees only their own note.
        hits3 = await rag.recall_relationship(s, customer_id=cust2.id, chef_id=chef.id, query="spicy")
        assert any("extra spicy" in h for h in hits3)
        assert all("less spicy" not in h for h in hits3)
    await engine.dispose()
