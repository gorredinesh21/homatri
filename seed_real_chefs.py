"""Seed the 4 REAL homemakers (Tally CSV 2026-09-04) into the database.

- Profiles with permanent GCS-hosted photos (bucket homatri-media-prod-503308)
- Menus parsed from the CSV with the standing +Rs.50 price adjustment, BOTH windows
- Photo reels (chef + dish/kitchen photos) published to the community feed
- Deactivates demo chefs so ONLY the real kitchens appear everywhere

Run against Cloud SQL via the auth proxy:
  DATABASE_URL=postgresql+asyncpg://dinesh:homatri_pass@localhost:5433/homatri_prod \
      python seed_real_chefs.py
"""

import asyncio
import logging
from decimal import Decimal

from sqlalchemy import delete, select, update

from backend.app.db.session import SessionFactory
from backend.app.models.chef import ChefMenuItem, ChefProfile
from backend.app.models.social import ChefReel

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("seed_real_chefs")

GCS = "https://storage.googleapis.com/homatri-media-prod-503308/chefs"
PRICE_BUMP = 50  # standing directive: listed price + Rs.50

# Menu lines exactly as the chefs wrote them in the CSV ("dish - price").
REAL_CHEFS = [
    dict(
        phone="9324332544",
        name="Shrushti Vivek Sutar",
        kitchen="घरची चव",
        bio="I love cooking. Aagri cuisine, chicken and prawns biryani, Chinese items — "
            "homemade food with Special Masala, made with love for 6 years.",
        hometown="Aagri Cuisine, Maharashtra",
        signature="Pineapple Sheera",
        photo=f"{GCS}/shrushti_vivek_sutar_chef.jpeg",
        lat="19.1197", lng="73.0078",
        featured=True,
        dishes=[
            ("Chapati Bhaji Salad", 70, "VEG"),
            ("Chapati, Dal, Rice, 2 Bhaji, Salad", 160, "VEG"),
            ("Chapati, Bhaji, Dal, Rice, Salad", 130, "VEG"),
            ("Dal, Rice, Bhaji, Salad", 90, "VEG"),
            ("Chicken Biryani", 180, "NON_VEG"),
            ("Prawns Biryani", 200, "NON_VEG"),
        ],
        gallery=[],
    ),
    dict(
        phone="9082885722",
        name="Hemlata Shravan Sutar",
        kitchen="AAGRI TADKA",
        bio="30 years of cooking food which gets appreciated by everyone. 100% authentic "
            "Aagri food with special homemade masalas.",
        hometown="Aagri Food, Maharashtra",
        signature="Pineapple Sheera",
        photo=f"{GCS}/hemlata_shravan_sutar_chef.jpg",
        lat="19.1215", lng="73.0090",
        dishes=[
            ("Bhaji, Chapati, Salad", 70, "VEG"),
            ("Chapati, Bhaji, Rice, Dal, Salad", 160, "VEG"),
            ("Dal, Bhaji, Rice, Salad", 90, "VEG"),
        ],
        gallery=[],
    ),
    dict(
        phone="9594524861",
        name="Prachika Lohar",
        kitchen="Malvan Kitchen",
        bio="I've been cooking for family for 20 years — that's the best experience. "
            "Authentic Malvani fish curry, coconut masalas and soft puran poli.",
        hometown="Malvani Coastal, Maharashtra",
        signature="Fish Curry",
        photo=f"{GCS}/prachika_lohar_chef.jpg",
        lat="19.1175", lng="73.0052",
        dishes=[
            ("Bhaji Chapati Salad", 70, "VEG"),
            ("Rice, Bhaji, Chapati, Dal, Salad", 160, "VEG"),
            ("Rice, Dal, Bhaji", 90, "VEG"),
            ("Malvani Fish Curry Thali", 200, "NON_VEG"),
        ],
        gallery=[],
    ),
    dict(
        phone="8693817484",
        name="Sumitra Dolas",
        kitchen="Dilse's Kitchen",
        bio="I'm Sumitra Dolas, 28. I love making new dishes — Maharashtrian food is our "
            "signature and Thecha Chicken Thali is the cherry on the cake. Our hygiene, "
            "quality and quantity keep customers happy and safe.",
        hometown="Maharashtrian Home Food",
        signature="Pav Bhaji",
        photo=f"{GCS}/sumitra_dolas_chef.jpg",
        lat="19.1230", lng="73.0105",
        dishes=[
            ("Puran Poli", 20, "VEG"),
            ("Ukdiche Modak", 20, "VEG"),
            ("Pav Bhaji", 90, "VEG"),
            ("Chicken and Roti", 120, "NON_VEG"),
        ],
        gallery=[
            (f"{GCS}/sumitra_dolas_dish.jpg", "Fresh pav bhaji from Dilse's Kitchen", "Pav Bhaji", 90),
            (f"{GCS}/sumitra_dolas_kitchen.jpg", "Our home kitchen — hygiene first", None, None),
            (f"{GCS}/sumitra_dolas_other.jpg", "Made with love, Dilse's Kitchen", None, None),
        ],
    ),
]

DEMO_PHONES = ["9876543210", "9876543211", "9876543212", "9876543213"]
REAL_PHONES = [c["phone"] for c in REAL_CHEFS]


async def seed() -> None:
    async with SessionFactory() as db:
        # 1) Upsert real chef profiles.
        for c in REAL_CHEFS:
            chef = await db.get(ChefProfile, c["phone"])
            fields = dict(
                chef_name=c["name"],
                kitchen_name=c["kitchen"],
                city="Navi Mumbai",
                address=f"{c['kitchen']}, Sector 6, Ghansoli, Navi Mumbai",
                address_line1=f"Sector 6, Ghansoli, Navi Mumbai",
                apartment_or_locality="Ghansoli",
                hometown_region=c["hometown"],
                bio=c["bio"],
                kitchen_bio=c["bio"],
                profile_image_url=c["photo"],
                avatar_url=c["photo"],
                latitude=Decimal(c["lat"]),
                longitude=Decimal(c["lng"]),
                daily_capacity=20,
                accepting_orders=True,
                active_status=True,
                is_verified=True,
                is_featured=bool(c.get("featured")),
                rating_average=Decimal("4.80"),
            )
            if chef is None:
                db.add(ChefProfile(chef_phone=c["phone"], **fields))
                logger.info("inserted %s", c["kitchen"])
            else:
                for k, v in fields.items():
                    setattr(chef, k, v)
                logger.info("updated %s", c["kitchen"])

        # 2) Replace menus (idempotent): CSV price + Rs.50, served BOTH windows.
        for c in REAL_CHEFS:
            await db.execute(
                delete(ChefMenuItem).where(ChefMenuItem.chef_phone == c["phone"])
            )
            for dish, price, tag in c["dishes"]:
                db.add(
                    ChefMenuItem(
                        chef_phone=c["phone"],
                        dish_name=dish,
                        description=f"{dish} — fresh from {c['kitchen']}",
                        unit_price=Decimal(str(price + PRICE_BUMP)),
                        meal_type="BOTH",
                        dietary_tag=tag,
                        is_available=True,
                    )
                )
            logger.info("menu %s: %d dishes (+Rs%d)", c["kitchen"], len(c["dishes"]), PRICE_BUMP)

        # 3) Photo reels (idempotent): remove previous seeded photo-reels for these chefs, re-add.
        for c in REAL_CHEFS:
            await db.execute(
                delete(ChefReel).where(
                    ChefReel.chef_phone == c["phone"],
                    ChefReel.video_url.like(f"{GCS}%"),
                )
            )
            db.add(
                ChefReel(
                    chef_phone=c["phone"],
                    video_url=c["photo"],
                    thumbnail_url=c["photo"],
                    title=f"Meet {c['name'].split()[0]} — {c['kitchen']}",
                    dish_tag_name=c["signature"],
                    published=True,
                    likes_count=0,
                    comments_count=0,
                    view_count=0,
                )
            )
            for url, caption, dish, price in c.get("gallery", []):
                db.add(
                    ChefReel(
                        chef_phone=c["phone"],
                        video_url=url,
                        thumbnail_url=url,
                        title=caption,
                        dish_tag_name=dish,
                        dish_tag_price=Decimal(str(price + PRICE_BUMP)) if price else None,
                        published=True,
                    )
                )
            logger.info("reels %s: %d", c["kitchen"], 1 + len(c.get("gallery", [])))

        # 4) Hide demo chefs everywhere (active_status=False removes them from all feeds).
        await db.execute(
            update(ChefProfile)
            .where(ChefProfile.chef_phone.in_(DEMO_PHONES))
            .values(active_status=False, accepting_orders=False)
        )
        logger.info("demo chefs hidden: %s", ", ".join(DEMO_PHONES))

        # Safety: any chef that is NOT real and NOT a known demo also gets hidden.
        others = (
            await db.execute(
                select(ChefProfile.chef_phone).where(
                    ChefProfile.active_status.is_(True),
                    ChefProfile.chef_phone.notin_(REAL_PHONES),
                )
            )
        ).scalars().all()
        if others:
            await db.execute(
                update(ChefProfile)
                .where(ChefProfile.chef_phone.in_(list(others)))
                .values(active_status=False, accepting_orders=False)
            )
            logger.info("extra non-real chefs hidden: %s", ", ".join(others))

        await db.commit()
        logger.info("real chef seed complete — 4 kitchens live")


if __name__ == "__main__":
    asyncio.run(seed())
