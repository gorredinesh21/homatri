"""Additive migration: usernames, nested reel replies, community DMs."""

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
from backend.app.db.base import Base  # noqa: E402
import backend.app.models  # noqa: E402, F401


async def column_names(conn, table: str) -> set[str]:
    rows = await conn.execute(
        text(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = :table
            """
        ),
        {"table": table},
    )
    return {r[0] for r in rows.fetchall()}


async def add_columns(conn, table: str, columns: list[tuple[str, str]]) -> None:
    existing = await column_names(conn, table)
    for name, ddl in columns:
        if name in existing:
            continue
        await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
        print(f"  + {table}.{name}")


async def main() -> None:
    url = settings.database_url
    host = url.split("@")[-1] if "@" in url else url
    print(f"Migrating {host}")
    engine = create_async_engine(url, pool_pre_ping=True)
    async with engine.begin() as conn:
        await add_columns(
            conn,
            "customer_profiles",
            [
                ("username", "VARCHAR(50)"),
                ("role", "VARCHAR(20) DEFAULT 'CUSTOMER'"),
                ("password_hash", "VARCHAR(255)"),
            ],
        )
        await conn.execute(
            text(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS uq_customer_profiles_username
                ON customer_profiles (username)
                WHERE username IS NOT NULL
                """
            )
        )
        await add_columns(
            conn,
            "reel_comments",
            [
                ("username", "VARCHAR(50)"),
                ("avatar_url", "TEXT"),
                ("parent_comment_id", "UUID"),
                ("likes_count", "INTEGER DEFAULT 0"),
            ],
        )
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()
    print("reels/chat/username migration complete")


if __name__ == "__main__":
    asyncio.run(main())
