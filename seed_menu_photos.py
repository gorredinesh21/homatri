"""Seed real chef menus + menu-photo reels from the printed menu photos (2026-09).

Prices are taken AS PRINTED in each kitchen's menu photo (no bump) — the printed
menus already say "inclusive of delivery"; the platform adds only the Rs.11
convenience fee. Run with the Cloud SQL proxy on :5433.

  DATABASE_URL=postgresql+asyncpg://dinesh:homatri_pass@localhost:5433/homatri_prod \
  python -m seed_menu_photos
"""

import asyncio
import logging
from decimal import Decimal

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from backend.app.models.chef import ChefMenuItem
from backend.app.models.customer import CustomerOrderItem
from backend.app.models.social import ChefReel
import os

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("seed_menu_photos")

GCS = "https://storage.googleapis.com/homatri-media-prod-503308/chefs"

# (dish, price, dietary_tag)
SHARED_THALI = [
    ("Veg Thali (Full)", 119, "VEG"),
    ("Veg Thali (Mini)", 99, "VEG"),
    ("Bhaji Chapati", 79, "VEG"),
    ("Egg Curry Thali", 129, "NON_VEG"),
    ("Chicken Curry Rice", 169, "NON_VEG"),
    ("Chicken Curry Thali", 199, "NON_VEG"),
]

DILSE_MENU = [
    ("Dal-Roti Thali · Cabbage Sabji", 119, "VEG"),
    ("Dal-Roti Thali · Tondli Sabji", 119, "VEG"),
    ("Dal-Roti Thali · Alu-Mater Sabji", 119, "VEG"),
    ("Curd Curry Meal (Rice + Peanut Chutney)", 109, "VEG"),
    ("Dal-Roti Thali · Carrot & Beetroot Sabji", 119, "VEG"),
    ("Paneer Bhurji Thali", 129, "VEG"),
    ("Dal-Roti Thali · Beans Sabji", 109, "VEG"),
    ("Kharda Chicken Thali", 169, "NON_VEG"),
]

THALI_DESC = {
    "Veg Thali (Full)": "Dal, chawal, 3 chapati, sabji, salad",
    "Veg Thali (Mini)": "Dal, chawal, sabji, salad",
    "Bhaji Chapati": "Sabji with 3 chapati",
    "Egg Curry Thali": "Egg curry, rice, 3 chapati",
    "Chicken Curry Rice": "Chicken curry with rice",
    "Chicken Curry Thali": "Chicken curry, rice, 3 chapati",
}
DILSE_DESC = {
    "Dal-Roti Thali · Cabbage Sabji": "Dal, rice, roti, salad, cabbage sabji",
    "Dal-Roti Thali · Tondli Sabji": "Dal, rice, roti, salad, tondli sabji",
    "Dal-Roti Thali · Alu-Mater Sabji": "Dal, rice, roti, salad, alu-mater sabji",
    "Curd Curry Meal (Rice + Peanut Chutney)": "Rice, curd curry, salad, peanut chutney",
    "Dal-Roti Thali · Carrot & Beetroot Sabji": "Dal, rice, roti, salad, carrot & beetroot sabji",
    "Paneer Bhurji Thali": "Dal, rice, roti, paneer bhurji, salad",
    "Dal-Roti Thali · Beans Sabji": "Dal, rice, roti, salad, beans sabji",
    "Kharda Chicken Thali": "Dal, rice, roti, salad, kharda chicken",
}

CHEFS = [
    {"phone": "9324332544", "kitchen": "घरची चव (Gharchi Chav)", "menu": SHARED_THALI, "desc": THALI_DESC, "photo": f"{GCS}/menus/gharchi-chav-menu.jpg"},
    {"phone": "9082885722", "kitchen": "AAGRI TADKA", "menu": SHARED_THALI, "desc": THALI_DESC, "photo": f"{GCS}/menus/aagri-tadka-menu.jpg"},
    {"phone": "9594524861", "kitchen": "Malvan Kitchen", "menu": SHARED_THALI, "desc": THALI_DESC, "photo": f"{GCS}/menus/malvan-kitchen-menu.jpg"},
    {"phone": "8693817484", "kitchen": "Dilse's Kitchen", "menu": DILSE_MENU, "desc": DILSE_DESC, "photo": f"{GCS}/menus/dilse-kitchen-menu.jpg"},
]


async def main() -> None:
    url = os.environ.get(
        "DATABASE_URL",
        "postgresql+asyncpg://dinesh:homatri_pass@localhost:5433/homatri_prod",
    )
    engine = create_async_engine(url)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async with Session() as db:
        # menu_item_id is a NOT NULL FK from customer_order_items (which keep
        # their own dish/price snapshots), so rows referenced by past orders are
        # repurposed in place; unreferenced rows are deleted.
        referenced_ids = {
            row[0]
            for row in (
                await db.execute(select(CustomerOrderItem.menu_item_id).distinct())
            ).all()
        }

        for c in CHEFS:
            existing = (
                await db.execute(
                    select(ChefMenuItem).where(ChefMenuItem.chef_phone == c["phone"])
                )
            ).scalars().all()
            keep = [e for e in existing if e.menu_item_id in referenced_ids]
            drop = [e for e in existing if e.menu_item_id not in referenced_ids]
            for e in drop:
                await db.delete(e)

            for i, (dish, price, tag) in enumerate(c["menu"]):
                if i < len(keep):
                    e = keep[i]
                    e.dish_name = dish
                    e.description = c["desc"].get(dish, dish)
                    e.unit_price = Decimal(str(price))
                    e.meal_type = "BOTH"
                    e.dietary_tag = tag
                    e.is_available = True
                else:
                    db.add(
                        ChefMenuItem(
                            chef_phone=c["phone"],
                            dish_name=dish,
                            description=c["desc"].get(dish, dish),
                            unit_price=Decimal(str(price)),
                            meal_type="BOTH",
                            dietary_tag=tag,
                            is_available=True,
                        )
                    )
            logger.info(
                "menu %s: %d dishes (photo prices; repurposed %d, dropped %d)",
                c["kitchen"], len(c["menu"]), len(keep), len(drop),
            )

            # 2) Menu photo as a published photo-reel (idempotent).
            await db.execute(
                delete(ChefReel).where(
                    ChefReel.chef_phone == c["phone"],
                    ChefReel.video_url == c["photo"],
                )
            )
            db.add(
                ChefReel(
                    chef_phone=c["phone"],
                    video_url=c["photo"],
                    thumbnail_url=c["photo"],
                    title=f"Menu — {c['kitchen']}",
                    dish_tag_name=None,
                    published=True,
                    likes_count=0,
                    comments_count=0,
                    view_count=0,
                )
            )
            logger.info("reel %s: menu photo %s", c["kitchen"], c["photo"])

        await db.commit()
    await engine.dispose()
    logger.info("done")


if __name__ == "__main__":
    asyncio.run(main())
