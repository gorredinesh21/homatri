"""Phase-1 payments migration: add payment_method / payment_status to customer_orders.

Idempotent — safe to run against dev, staging or Cloud SQL (via Auth Proxy).
Run:  python -m backend.scripts.migrate_payments
"""

from __future__ import annotations

import asyncio
import logging

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import VARCHAR as PG_VARCHAR

from backend.app.db.session import engine

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("migrate_payments")

COLUMNS = [
    (
        "customer_orders",
        "payment_method",
        sa.Column("payment_method", PG_VARCHAR(20), nullable=False, server_default="RAZORPAY"),
    ),
    (
        "customer_orders",
        "payment_status",
        sa.Column("payment_status", PG_VARCHAR(20), nullable=False, server_default="PENDING"),
    ),
]


async def main() -> None:
    async with engine.begin() as conn:
        for table, column, col_def in COLUMNS:
            has = await conn.scalar(
                sa.text(
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_name = :t AND column_name = :c"
                ),
                {"t": table, "c": column},
            )
            if has:
                log.info("skip %s.%s (already present)", table, column)
                continue
            await conn.execute(sa.text(f'ALTER TABLE "{table}" ADD COLUMN "{column}" {col_def.type.compile()} NOT NULL DEFAULT \'{col_def.server_default.arg}\''))
            log.info("added %s.%s", table, column)
    await engine.dispose()

    # Phase-2: persistent dietary requests table (idempotent via checkfirst).
    from backend.app.db.base import Base
    import backend.app.models.chef  # noqa: F401 — register mappers

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all, tables=[Base.metadata.tables["chef_dietary_requests"]])
        log.info("chef_dietary_requests table ensured.")
    await engine.dispose()
    log.info("payments migration complete.")


if __name__ == "__main__":
    asyncio.run(main())
