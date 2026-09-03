"""Add order delivery columns and driver_location_pings if missing."""

from __future__ import annotations

import asyncio

from sqlalchemy import text

from backend.app.db.session import engine


STATEMENTS = [
    "ALTER TABLE customer_orders ADD COLUMN IF NOT EXISTS delivery_address TEXT",
    "ALTER TABLE customer_orders ADD COLUMN IF NOT EXISTS delivery_latitude NUMERIC(10, 8)",
    "ALTER TABLE customer_orders ADD COLUMN IF NOT EXISTS delivery_longitude NUMERIC(11, 8)",
    "ALTER TABLE customer_orders ADD COLUMN IF NOT EXISTS delivery_otp VARCHAR(6)",
    "ALTER TABLE customer_orders ADD COLUMN IF NOT EXISTS delivery_otp_verified_at TIMESTAMPTZ",
]


async def main() -> None:
    async with engine.begin() as conn:
        for sql in STATEMENTS:
            await conn.execute(text(sql))
        from backend.app.db.base import Base
        import backend.app.models  # noqa: F401

        await conn.run_sync(Base.metadata.create_all)
    print("ops schema ready")


if __name__ == "__main__":
    asyncio.run(main())
