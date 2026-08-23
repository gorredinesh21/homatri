"""Phase 1 additive schema migration for Cloud SQL PostgreSQL.

Adds onboarding columns to existing phone-keyed profile tables and creates
auth, bulk catering, and social reel tables. Safe to re-run.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

# Load production env when DATABASE_URL is not already set.
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


PROFILE_COLUMNS: dict[str, list[tuple[str, str]]] = {
    "customer_profiles": [
        ("id", "UUID"),
        ("full_name", "VARCHAR(100)"),
        ("address_line1", "VARCHAR(255)"),
        ("address_line2", "VARCHAR(255)"),
        ("postal_code", "VARCHAR(20)"),
        ("google_sub", "VARCHAR(255)"),
        ("avatar_url", "TEXT"),
        ("is_cartoon_avatar", "BOOLEAN DEFAULT TRUE"),
        ("dietary_preferences", "JSONB DEFAULT '[\"PURE_VEG\"]'::jsonb"),
        ("deleted_at", "TIMESTAMPTZ"),
    ],
    "chef_profiles": [
        ("id", "UUID"),
        ("address_line1", "VARCHAR(255)"),
        ("hometown_region", "VARCHAR(100)"),
        ("bio", "TEXT"),
        ("avatar_url", "TEXT"),
        ("payout_upi_id", "VARCHAR(100)"),
        ("rating_average", "NUMERIC(3,2) DEFAULT 4.80"),
        ("daily_capacity", "INTEGER DEFAULT 15"),
        ("accepting_orders", "BOOLEAN DEFAULT TRUE"),
        ("deleted_at", "TIMESTAMPTZ"),
    ],
    "driver_profiles": [
        ("id", "UUID"),
        ("driving_license_number", "VARCHAR(50) DEFAULT 'PENDING'"),
        ("vehicle_reg_number", "VARCHAR(30) DEFAULT 'PENDING'"),
        ("assigned_cluster", "VARCHAR(100) DEFAULT 'Ghansoli'"),
        ("payout_upi_id", "VARCHAR(100)"),
        ("driver_photo_url", "TEXT"),
        ("on_shift", "BOOLEAN DEFAULT FALSE"),
        ("is_active", "BOOLEAN DEFAULT TRUE"),
    ],
}


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


async def add_missing_columns(conn) -> None:
    await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
    for table, cols in PROFILE_COLUMNS.items():
        existing = await column_names(conn, table)
        if not existing:
            print(f"SKIP {table}: table not found")
            continue
        for name, ddl in cols:
            if name in existing:
                continue
            await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
            print(f"ADD {table}.{name}")

    # Widen email if present.
    cust_cols = await column_names(conn, "customer_profiles")
    if "email" in cust_cols:
        await conn.execute(
            text("ALTER TABLE customer_profiles ALTER COLUMN email TYPE VARCHAR(255)")
        )


async def backfill(conn) -> None:
    await conn.execute(
        text("UPDATE customer_profiles SET id = gen_random_uuid() WHERE id IS NULL")
    )
    await conn.execute(
        text("UPDATE customer_profiles SET full_name = name WHERE full_name IS NULL")
    )
    await conn.execute(
        text(
            "UPDATE customer_profiles SET address_line1 = delivery_address "
            "WHERE address_line1 IS NULL"
        )
    )
    await conn.execute(
        text("UPDATE customer_profiles SET postal_code = pincode WHERE postal_code IS NULL")
    )
    await conn.execute(
        text(
            "UPDATE customer_profiles SET dietary_preferences = '[\"PURE_VEG\"]'::jsonb "
            "WHERE dietary_preferences IS NULL"
        )
    )
    await conn.execute(
        text(
            "UPDATE customer_profiles SET is_cartoon_avatar = TRUE WHERE is_cartoon_avatar IS NULL"
        )
    )

    await conn.execute(text("UPDATE chef_profiles SET id = gen_random_uuid() WHERE id IS NULL"))
    await conn.execute(
        text("UPDATE chef_profiles SET address_line1 = address WHERE address_line1 IS NULL")
    )
    await conn.execute(text("UPDATE chef_profiles SET bio = kitchen_bio WHERE bio IS NULL"))
    await conn.execute(
        text(
            "UPDATE chef_profiles SET avatar_url = profile_image_url "
            "WHERE avatar_url IS NULL AND profile_image_url IS NOT NULL"
        )
    )
    await conn.execute(
        text(
            "UPDATE chef_profiles SET fssai_license_number = 'PENDING' "
            "WHERE fssai_license_number IS NULL OR btrim(fssai_license_number) = ''"
        )
    )
    await conn.execute(
        text("UPDATE chef_profiles SET accepting_orders = TRUE WHERE accepting_orders IS NULL")
    )
    await conn.execute(
        text("UPDATE chef_profiles SET daily_capacity = 15 WHERE daily_capacity IS NULL")
    )
    await conn.execute(
        text("UPDATE chef_profiles SET rating_average = 4.80 WHERE rating_average IS NULL")
    )

    await conn.execute(text("UPDATE driver_profiles SET id = gen_random_uuid() WHERE id IS NULL"))
    await conn.execute(
        text(
            "UPDATE driver_profiles SET vehicle_reg_number = vehicle_number "
            "WHERE vehicle_reg_number IS NULL OR vehicle_reg_number = 'PENDING'"
        )
    )
    await conn.execute(
        text(
            "UPDATE driver_profiles SET driving_license_number = COALESCE(driver_license_number, 'PENDING') "
            "WHERE driving_license_number IS NULL OR driving_license_number = 'PENDING'"
        )
    )
    await conn.execute(
        text("UPDATE driver_profiles SET on_shift = is_on_shift WHERE on_shift IS NULL")
    )
    await conn.execute(
        text("UPDATE driver_profiles SET is_active = active_status WHERE is_active IS NULL")
    )


async def tighten_constraints(conn) -> None:
    await conn.execute(text("ALTER TABLE customer_profiles ALTER COLUMN id SET NOT NULL"))
    await conn.execute(text("ALTER TABLE chef_profiles ALTER COLUMN id SET NOT NULL"))
    await conn.execute(text("ALTER TABLE driver_profiles ALTER COLUMN id SET NOT NULL"))
    await conn.execute(
        text("ALTER TABLE chef_profiles ALTER COLUMN fssai_license_number SET NOT NULL")
    )
    await conn.execute(
        text("ALTER TABLE driver_profiles ALTER COLUMN vehicle_reg_number SET NOT NULL")
    )
    await conn.execute(
        text("ALTER TABLE driver_profiles ALTER COLUMN driving_license_number SET NOT NULL")
    )

    await conn.execute(
        text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_customer_profiles_id ON customer_profiles (id)"
        )
    )
    await conn.execute(
        text("CREATE UNIQUE INDEX IF NOT EXISTS uq_chef_profiles_id ON chef_profiles (id)")
    )
    await conn.execute(
        text("CREATE UNIQUE INDEX IF NOT EXISTS uq_driver_profiles_id ON driver_profiles (id)")
    )
    await conn.execute(
        text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_customer_profiles_google_sub "
            "ON customer_profiles (google_sub) WHERE google_sub IS NOT NULL"
        )
    )
    await conn.execute(
        text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_customer_profiles_email "
            "ON customer_profiles (email) WHERE email IS NOT NULL AND email <> ''"
        )
    )
    await conn.execute(
        text("CREATE INDEX IF NOT EXISTS ix_chef_profiles_hometown_region ON chef_profiles (hometown_region)")
    )
    await conn.execute(
        text("CREATE INDEX IF NOT EXISTS ix_chef_profiles_accepting_orders ON chef_profiles (accepting_orders)")
    )


async def verify(conn) -> None:
    tables = await conn.execute(
        text(
            """
            SELECT tablename FROM pg_catalog.pg_tables
            WHERE schemaname = 'public'
            ORDER BY tablename
            """
        )
    )
    names = [r[0] for r in tables.fetchall()]
    print("\n=== public tables ===")
    for n in names:
        print(f"  {n}")
    required = [
        "customer_profiles",
        "chef_profiles",
        "driver_profiles",
        "otp_verifications",
        "user_sessions",
        "chef_catering_templates",
        "catering_template_items",
        "bulk_catering_orders",
        "chef_reels",
        "reel_comments",
        "reel_likes",
        "chef_followers",
    ]
    missing = [t for t in required if t not in names]
    if missing:
        raise SystemExit(f"MISSING TABLES: {missing}")
    print("\nPhase 1 required tables: OK")

    db = await conn.execute(text("SELECT current_database(), inet_server_addr(), current_user"))
    row = db.one()
    print(f"Connected: db={row[0]} host={row[1]} user={row[2]}")


async def main() -> None:
    url = settings.database_url
    host = url.split("@")[-1] if "@" in url else url
    print(f"Migrating {host}")
    engine = create_async_engine(url, pool_pre_ping=True)
    async with engine.begin() as conn:
        await add_missing_columns(conn)
        await backfill(conn)
        await tighten_constraints(conn)
        await conn.run_sync(Base.metadata.create_all)
        await verify(conn)
    await engine.dispose()
    print("Phase 1 migration complete.")


if __name__ == "__main__":
    asyncio.run(main())
