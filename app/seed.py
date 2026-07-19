"""Seed baseline demo data (one chef kitchen, one driver, one customer, menu).

Idempotent: only seeds when the users table is empty. Used on startup and by the
``/api/reset`` endpoint (which wipes first).
"""
from __future__ import annotations

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.entities import (
    Chef,
    ConversationState,
    Delivery,
    Driver,
    KnowledgeEmbedding,
    MenuItem,
    Order,
    OrderChangeRequest,
    OrderItem,
    Payment,
    RelationshipMemory,
    User,
)
from app.models.enums import UserRole

log = get_logger("seed")

MENU = [
    ("Butter Roti", 20.0, "Fresh whole wheat roti with ghee"),
    ("Paneer Butter Masala", 120.0, "Rich paneer curry in butter gravy"),
    ("Dal Fry", 90.0, "Yellow lentil tempering"),
    ("Chapati", 15.0, "Soft wheat flatbread"),
    ("Jeera Rice", 80.0, "Basmati rice cooked with cumin"),
]


async def seed_if_empty(session: AsyncSession) -> bool:
    count = (await session.execute(select(func.count(User.id)))).scalar_one()
    if count:
        return False
    await _seed(session)
    return True


async def reset_and_seed(session: AsyncSession) -> None:
    # order matters for FK integrity
    for model in (
        KnowledgeEmbedding, RelationshipMemory, ConversationState, OrderChangeRequest,
        Payment, Delivery, OrderItem, Order, MenuItem, Chef, Driver, User,
    ):
        await session.execute(delete(model))
    await _seed(session)
    await session.commit()


async def _seed(session: AsyncSession) -> None:
    customer = User(phone="+919876543210", name="Rohan Dev", role=UserRole.CUSTOMER)
    chef_user = User(phone="+919999888877", name="Kiran Sharma", role=UserRole.CHEF)
    driver_user = User(phone="+918888777766", name="Suresh Kumar", role=UserRole.DRIVER)
    session.add_all([customer, chef_user, driver_user])
    await session.flush()

    chef = Chef(
        user_id=chef_user.id,
        kitchen_name="Sharma's Kitchen",
        kitchen_address="Flat 402, Shanti Sadan, Indiranagar, Bengaluru",
        gps_coordinates="12.9719,77.6412",
        is_active=True,
        max_daily_capacity=20,
    )
    driver = Driver(
        user_id=driver_user.id,
        vehicle_type="Two-Wheeler (Ather)",
        license_plate="KA-03-EX-9988",
        is_available=True,
        current_gps_coordinates="12.9730,77.6400",
    )
    session.add_all([chef, driver])
    await session.flush()

    for name, price, desc in MENU:
        session.add(MenuItem(chef_id=chef.id, name=name, price=price, description=desc))
    await session.commit()
    log.info("seeded demo data")
