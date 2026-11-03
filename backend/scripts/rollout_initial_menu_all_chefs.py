"""Rollout: replace every active kitchen's menu with the initial category catalog.

For each non-deleted chef:
  1. Load the initial 33-dish catalog (skips dishes already present).
  2. Ensure every catalog dish is AVAILABLE (chefs can supply everything).
  3. ARCHIVE every live dish that is not part of the catalog — archived dishes are
     hidden from the chef dashboard, customers and order validation, while past
     order history keeps its snapshots. Fully reversible (flip is_archived).
  4. Unassign the "Kitchen Specials" fallback category once it holds no live dishes,
     so the chef's Menu Manager shows exactly the 8 initial categories.

Idempotent — safe to re-run.

Run on prod (via cloud-sql-proxy :5433):
  DATABASE_URL=postgresql+asyncpg://dinesh:homatri_pass@127.0.0.1:5433/homatri_prod \
      python3 backend/scripts/rollout_initial_menu_all_chefs.py
"""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
sys.path.insert(0, os.path.dirname(__file__))

from sqlalchemy import select  # noqa: E402

import backend.app.models  # noqa: F401,E402
from backend.app.db.session import SessionFactory  # noqa: E402
from backend.app.models.chef import (  # noqa: E402
    ChefCategoryAssignment,
    ChefMenuCategory,
    ChefMenuItem,
    ChefProfile,
)
from migrate_category_menu import INITIAL_MENU, load_initial_menu, seed_categories  # noqa: E402

FALLBACK_CATEGORY = "Kitchen Specials"


async def main() -> None:
    catalog_names = {
        name for dishes in INITIAL_MENU.values() for (name, _price, _diet) in dishes
    }
    async with SessionFactory() as session:
        async with session.begin():
            categories = await seed_categories(session)
            chefs = (
                await session.execute(
                    select(ChefProfile)
                    .where(ChefProfile.deleted_at.is_(None))
                    .order_by(ChefProfile.kitchen_name)
                )
            ).scalars().all()
            fallback = (
                await session.execute(
                    select(ChefMenuCategory).where(ChefMenuCategory.name == FALLBACK_CATEGORY)
                )
            ).scalar_one_or_none()

            print(f"== Menu override rollout: {len(chefs)} non-deleted kitchens ==\n")
            for chef in chefs:
                created, skipped = await load_initial_menu(session, chef.chef_phone, categories)
                dishes = (
                    await session.execute(
                        select(ChefMenuItem).where(
                            ChefMenuItem.chef_phone == chef.chef_phone,
                            ChefMenuItem.is_archived.is_(False),
                        )
                    )
                ).scalars().all()
                archived = made_available = 0
                for d in dishes:
                    if d.dish_name in catalog_names:
                        if not d.is_available:
                            d.is_available = True
                            made_available += 1
                    else:
                        d.is_archived = True
                        d.is_available = False
                        archived += 1
                live = [d for d in dishes if not d.is_archived]
                # Drop the fallback category from this chef once it holds nothing live.
                fallback_removed = False
                if fallback is not None:
                    still_used = any(d.category_id == fallback.category_id for d in live)
                    if not still_used:
                        assignment = (
                            await session.execute(
                                select(ChefCategoryAssignment).where(
                                    ChefCategoryAssignment.chef_phone == chef.chef_phone,
                                    ChefCategoryAssignment.category_id == fallback.category_id,
                                )
                            )
                        ).scalar_one_or_none()
                        if assignment is not None:
                            await session.delete(assignment)
                            fallback_removed = True
                available_ct = sum(1 for d in live if d.is_available)
                print(
                    f"{chef.kitchen_name} ({chef.chef_phone}): live={len(live)} "
                    f"(all available={available_ct}) | catalog added={created} kept={skipped} | "
                    f"old archived={archived} | made available={made_available} | "
                    f"kitchen-specials removed={'yes' if fallback_removed else 'no/already gone'}"
                )
    print("\n== rollout complete ==")


if __name__ == "__main__":
    asyncio.run(main())
