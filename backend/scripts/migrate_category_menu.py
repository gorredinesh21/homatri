"""Category-based menu migration + initial catalog seed. Idempotent — safe to re-run.

What it does, in order:
  1. create_all() — creates chef_menu_categories / chef_category_assignments (new tables).
  2. ALTER TABLE ... ADD COLUMN IF NOT EXISTS for the new columns on existing tables:
       chef_menu_items.category_id + display_order, customer_orders.combo_discount,
       customer_order_items.combo_category_id.
  3. Seeds the 8 initial Homatri categories (exact names/prices from the menu spec).
  4. Assigns the 8 categories to every non-deleted chef (idempotent).
  5. Fallback: any live dish with no category goes into a per-platform "Kitchen
     Specials" category, assigned to its chef — nothing is dropped or orphaned.
  6. Optional: --load-initial-menu <chef_phone> creates the full initial dish
     catalog on one chef's menu (skips dishes that already exist by name).

Run locally:  DATABASE_URL=postgresql+asyncpg://dinesh:homatri_pass@localhost:5432/homatri_dev \
                  python3 backend/scripts/migrate_category_menu.py [--load-initial-menu 9876543210]
"""

from __future__ import annotations

import asyncio
import os
import sys
from decimal import Decimal

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from sqlalchemy import inspect, select, text  # noqa: E402

import backend.app.models  # noqa: F401,E402  (register all tables)
from backend.app.core.ids import generate_id  # noqa: E402
from backend.app.db.base import Base  # noqa: E402
from backend.app.db.session import SessionFactory, engine  # noqa: E402
from backend.app.models.chef import (  # noqa: E402
    ChefCategoryAssignment,
    ChefMenuCategory,
    ChefMenuItem,
    ChefProfile,
)

FALLBACK_CATEGORY = "Kitchen Specials"

INITIAL_CATEGORIES = [
    dict(name="Mini Rice Bowls", description="Comfort rice bowls starting at ₹99", display_order=1),
    dict(name="Pocket Saver Combos", description="Filling combos starting at ₹120", display_order=2),
    dict(name="Protein Bowl (Women)", description="High-protein meals starting at ₹140", display_order=3),
    dict(name="Daily Meals", description="Full thalis starting at ₹149", display_order=4),
    dict(name="Bhole Chature", description="Chhole-paratha specials starting at ₹140", display_order=5),
    dict(name="Paneer Paradise", description="Everything paneer — ₹199 flat", display_order=6),
    dict(
        name="Bhookh Buddies",
        description="Pick any two meals — ₹10 off the pair",
        display_order=7,
        pricing_type="PAIR_COMBO",
        combo_discount=Decimal("10.00"),
        combo_serves=2,
    ),
    dict(
        name="Pocket Friendly Feast",
        description="Pick any two meals — ₹20 off the pair",
        display_order=8,
        pricing_type="PAIR_COMBO",
        combo_discount=Decimal("20.00"),
        combo_serves=4,
    ),
]

# (category, dish, price, dietary)
INITIAL_MENU = {
    "Mini Rice Bowls": [
        ("Lemon Rice", 99, "VEG"),
        ("Masala Rice", 99, "VEG"),
        ("Maggi Masala", 99, "VEG"),
        ("Coconut Rice", 110, "VEG"),
        ("Curd Rice", 110, "VEG"),
        ("Dal Khichdi", 110, "VEG"),
        ("Tadke Wali Dal Khichdi", 120, "VEG"),
        ("Moong Dal Khichdi", 120, "VEG"),
        ("Palak Dal Khichdi", 120, "VEG"),
        ("Soyabean Pulao", 120, "VEG"),
        ("Matar Pulao", 120, "VEG"),
        ("Paneer Pulao", 140, "VEG"),
    ],
    "Pocket Saver Combos": [
        ("Dal Tadka + 3 Rotis", 120, "VEG"),
        ("3 Rotis + Dal + Jeera Aloo", 120, "VEG"),
        ("3 Rotis + Dal + Omelette", 120, "EGG"),
        ("Chana Masala + 3 Rotis", 120, "VEG"),
        ("4 Pooris + Aloo Tamatar Sabji", 120, "VEG"),
        ("Anda Bhurji + Roti", 120, "EGG"),
    ],
    "Protein Bowl (Women)": [
        ("Paneer Sabji + 3 Rotis", 150, "VEG"),
        ("Paneer Bhurji + 3 Rotis", 150, "VEG"),
        ("Paneer Salad", 150, "VEG"),
    ],
    "Daily Meals": [
        ("Aloo Gobhi Sabji + Dal + Rice + 2 Rotis", 149, "VEG"),
        ("Bhindi Sabji + Dal + Rice + 2 Rotis", 149, "VEG"),
        ("Sabji + Dal + Rice + 2 Rotis", 149, "VEG"),
    ],
    "Bhole Chature": [
        ("Butter Aloo Paratha + Curd", 160, "VEG"),
        ("Chhole Bhatoore", 160, "VEG"),
        ("Dum Aloo Poori (6 pieces)", 160, "VEG"),
        ("Chhole Paratha (2 pieces)", 160, "VEG"),
        ("Methi Paratha", 140, "VEG"),
    ],
    "Paneer Paradise": [
        ("Matar Paneer + Roti", 199, "VEG"),
        ("Paneer Bhurji + 4 Rotis", 199, "VEG"),
        ("Palak Paneer + 4 Rotis", 199, "VEG"),
        ("Paneer Paratha (2 pieces)", 199, "VEG"),
    ],
}


async def ensure_columns() -> list[str]:
    """Add new columns to existing tables (idempotent). Returns actions taken."""
    added: list[str] = []
    statements = [
        ("chef_menu_items", "category_id",
         "ALTER TABLE chef_menu_items ADD COLUMN IF NOT EXISTS category_id VARCHAR(36) REFERENCES chef_menu_categories(category_id)"),
        ("chef_menu_items", "display_order",
         "ALTER TABLE chef_menu_items ADD COLUMN IF NOT EXISTS display_order INTEGER NOT NULL DEFAULT 0"),
        ("customer_orders", "combo_discount",
         "ALTER TABLE customer_orders ADD COLUMN IF NOT EXISTS combo_discount NUMERIC(10,2) NOT NULL DEFAULT 0"),
        ("customer_order_items", "combo_category_id",
         "ALTER TABLE customer_order_items ADD COLUMN IF NOT EXISTS combo_category_id VARCHAR(36)"),
    ]
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        for table, column, ddl in statements:
            def _has(sync_conn, table=table, column=column):
                insp = inspect(sync_conn)
                return column in {c["name"] for c in insp.get_columns(table)}

            exists = await conn.run_sync(_has)
            if not exists:
                await conn.execute(text(ddl))
                added.append(f"{table}.{column}")
            # indexes for the new FK columns (idempotent)
            await conn.execute(text(
                f"CREATE INDEX IF NOT EXISTS ix_{table}_{column} ON {table} ({column})"
            ))
    return added


async def seed_categories(session) -> dict[str, ChefMenuCategory]:
    by_name = {
        c.name: c
        for c in (await session.execute(select(ChefMenuCategory))).scalars().all()
    }
    created = []
    for spec in INITIAL_CATEGORIES:
        if spec["name"] in by_name:
            continue
        cat = ChefMenuCategory(
            category_id=generate_id("cat"),
            name=spec["name"],
            description=spec.get("description"),
            display_order=spec.get("display_order", 0),
            is_active=True,
            pricing_type=spec.get("pricing_type", "STANDARD"),
            combo_discount=spec.get("combo_discount"),
            combo_serves=spec.get("combo_serves"),
        )
        session.add(cat)
        by_name[spec["name"]] = cat
        created.append(cat.name)
    await session.flush()
    return by_name


async def assign_to_all_chefs(session, categories: dict[str, ChefMenuCategory]) -> tuple[int, int]:
    chefs = (
        await session.execute(select(ChefProfile).where(ChefProfile.deleted_at.is_(None)))
    ).scalars().all()
    existing = {
        (a.chef_phone, a.category_id)
        for a in (await session.execute(select(ChefCategoryAssignment))).scalars().all()
    }
    added = reactivated = 0
    initial_ids = [c.category_id for c in categories.values()]
    for chef in chefs:
        for cid in initial_ids:
            if (chef.chef_phone, cid) in existing:
                continue
            session.add(
                ChefCategoryAssignment(chef_phone=chef.chef_phone, category_id=cid)
            )
            added += 1
    # reactivate any deactivated assignments for the initial set
    rows = (
        await session.execute(select(ChefCategoryAssignment).where(ChefCategoryAssignment.is_active.is_(False)))
    ).scalars().all()
    for a in rows:
        if a.category_id in initial_ids:
            a.is_active = True
            reactivated += 1
    await session.flush()
    return added, reactivated


async def migrate_uncategorised_dishes(session, categories: dict[str, ChefMenuCategory]) -> int:
    orphan_dishes = (
        await session.execute(
            select(ChefMenuItem).where(ChefMenuItem.category_id.is_(None))
        )
    ).scalars().all()
    if not orphan_dishes:
        return 0
    fallback = (
        await session.execute(select(ChefMenuCategory).where(ChefMenuCategory.name == FALLBACK_CATEGORY))
    ).scalar_one_or_none()
    if fallback is None:
        fallback = ChefMenuCategory(
            category_id=generate_id("cat"),
            name=FALLBACK_CATEGORY,
            description="Dishes carried over from the previous menu",
            display_order=99,
            is_active=True,
            pricing_type="STANDARD",
        )
        session.add(fallback)
        await session.flush()
    affected_chefs = {d.chef_phone for d in orphan_dishes}
    existing = {
        (a.chef_phone, a.category_id)
        for a in (await session.execute(select(ChefCategoryAssignment))).scalars().all()
    }
    for phone in affected_chefs:
        if (phone, fallback.category_id) not in existing:
            session.add(ChefCategoryAssignment(chef_phone=phone, category_id=fallback.category_id))
    for d in orphan_dishes:
        d.category_id = fallback.category_id
    await session.flush()
    return len(orphan_dishes)


async def load_initial_menu(session, chef_phone: str, categories: dict[str, ChefMenuCategory]) -> tuple[int, int]:
    chef = await session.get(ChefProfile, chef_phone)
    if chef is None:
        raise SystemExit(f"Chef {chef_phone} not found — load initial menu onto an existing chef.")
    existing_names = {
        (d.dish_name, d.category_id)
        for d in (
            await session.execute(select(ChefMenuItem).where(ChefMenuItem.chef_phone == chef_phone))
        ).scalars().all()
    }
    created = skipped = 0
    for cat_name, dishes in INITIAL_MENU.items():
        cat = categories.get(cat_name)
        if cat is None:
            continue
        for order, (dish_name, price, dietary) in enumerate(dishes, 1):
            if (dish_name, cat.category_id) in existing_names:
                skipped += 1
                continue
            session.add(
                ChefMenuItem(
                    menu_item_id=generate_id("itm"),
                    chef_phone=chef_phone,
                    category_id=cat.category_id,
                    dish_name=dish_name,
                    description=f"From our {cat_name} menu",
                    unit_price=Decimal(str(price)),
                    meal_type="BOTH",
                    dietary_tag=dietary,
                    is_available=True,
                    max_availability=20,
                    display_order=order,
                )
            )
            created += 1
    await session.flush()
    return created, skipped


async def main() -> None:
    load_phone = None
    if "--load-initial-menu" in sys.argv:
        load_phone = sys.argv[sys.argv.index("--load-initial-menu") + 1]

    print("== Homatri category-menu migration ==")
    added = await ensure_columns()
    print(f"[1] schema: new tables created; columns added: {added or 'none (already present)'}")

    async with SessionFactory() as session:
        async with session.begin():
            categories = await seed_categories(session)
            print(f"[2] categories: {len(categories)} present (initial 8 ensured)")

            assigned, reactivated = await assign_to_all_chefs(session, categories)
            print(f"[3] assignments: +{assigned} new, {reactivated} reactivated for non-deleted chefs")

            migrated = await migrate_uncategorised_dishes(session, categories)
            print(f"[4] existing dishes: {migrated} filed under '{FALLBACK_CATEGORY}' (none dropped)")

            if load_phone:
                created, skipped = await load_initial_menu(session, load_phone, categories)
                print(f"[5] initial menu for {load_phone}: {created} dishes created, {skipped} already present")

            total_dishes = (
                await session.execute(select(ChefMenuItem).where(ChefMenuItem.is_archived.is_(False)))
            ).scalars()
            print(f"[6] live dishes now on file: {len(list(total_dishes))}")
    print("== migration complete ==")


if __name__ == "__main__":
    asyncio.run(main())
