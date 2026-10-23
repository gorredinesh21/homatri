"""Additive migration: chef_menu_items.is_archived (soft delete for menu dishes).

Dishes referenced by customer_order_items cannot be hard-deleted (FK), so the
chef "remove dish" endpoint archives them instead. Existing rows default to
false. Run BEFORE deploying a backend that references the column:

    DATABASE_URL=<prod url> python -m backend.scripts.migrate_menu_archive
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

_ROOT = Path("/home/dinesh/coding/PROJECTS/homatri")
if not os.environ.get("DATABASE_URL"):
    for candidate in (_ROOT / ".env.prod", _ROOT / ".env"):
        if candidate.exists():
            for line in candidate.read_text().splitlines():
                if line.startswith("DATABASE_URL=") and not line.strip().startswith("#"):
                    os.environ["DATABASE_URL"] = line.split("=", 1)[1].strip()
                    break
            if os.environ.get("DATABASE_URL"):
                break

from backend.app.core.config import settings  # noqa: E402


async def main() -> None:
    url = settings.database_url
    host = url.split("@")[-1] if "@" in url else url
    print(f"Migrating {host}")
    engine = create_async_engine(url, pool_pre_ping=True)
    async with engine.begin() as conn:
        rows = await conn.execute(
            text(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_schema = 'public' AND table_name = 'chef_menu_items'
                """
            )
        )
        existing = {r[0] for r in rows.fetchall()}
        if "is_archived" in existing:
            print("  chef_menu_items.is_archived already present — nothing to do")
        else:
            await conn.execute(
                text(
                    "ALTER TABLE chef_menu_items "
                    "ADD COLUMN is_archived BOOLEAN NOT NULL DEFAULT false"
                )
            )
            print("  + chef_menu_items.is_archived")
    await engine.dispose()
    print("menu archive migration complete")


if __name__ == "__main__":
    asyncio.run(main())
