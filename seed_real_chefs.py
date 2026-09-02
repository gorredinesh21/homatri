"""Seed script to insert 4 real homemakers from Tally onboarding CSV into PostgreSQL with +50 price adjustment."""

import asyncio
import logging
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import select
from backend.app.db.session import SessionFactory
from backend.app.models.chef import ChefProfile

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("seed_chefs")

REAL_CHEFS_DATA = [
    {
        "phone": "9324332544",
        "name": "Shrushti Vivek Sutar",
        "kitchen_name": "घरची चव (Gharachi Chav)",
        "city": "Navi Mumbai",
        "cluster": "Ghansoli",
        "address": "Sector 6, Ghansoli, Navi Mumbai",
        "hometown": "Aagri & Konkan",
        "bio": "Specialist in authentic Aagri thalis and fresh coastal biryanis. Cooking home meals with love for 6 years.",
        "fssai": "FSSAI 21524089000142",
        "photo_url": "https://storage.tally.so/private/e8b5c46b-58e3-4f75-be44-e166770a78ae.jpeg",
        "signature_dish": "Chicken Biryani & Pineapple Sheera",
        "lunch_price": 210.00,  # 160 + 50
        "dinner_price": 170.00, # 120 + 50
        "dietary": "NON_VEG",
        "rating": 4.9,
    },
    {
        "phone": "9082885722",
        "name": "Hemlata Shravan Sutar",
        "kitchen_name": "AAGRI TADKA",
        "city": "Navi Mumbai",
        "cluster": "Ghansoli",
        "address": "Sector 5, Ghansoli, Navi Mumbai",
        "hometown": "Aagri Region",
        "bio": "30 years of culinary experience in 100% authentic Aagri food. Special homemade masalas ground fresh.",
        "fssai": "FSSAI 21524089000198",
        "photo_url": "https://storage.tally.so/private/unnamed.jpg",
        "signature_dish": "Pineapple Sheera & Aagri Thali",
        "lunch_price": 210.00,  # 160 + 50
        "dinner_price": 140.00, # 90 + 50
        "dietary": "NON_VEG",
        "rating": 4.9,
    },
    {
        "phone": "9594524861",
        "name": "Prachika Lohar",
        "kitchen_name": "Malvan Kitchen",
        "city": "Navi Mumbai",
        "cluster": "Ghansoli",
        "address": "Sector 4, Ghansoli, Navi Mumbai",
        "hometown": "Malvan Coastal",
        "bio": "20 years of cooking authentic Malvani fish curry, coconut masalas, and soft puran polis.",
        "fssai": "FSSAI 21524089000210",
        "photo_url": "https://storage.tally.so/private/IMG-20260901-WA0013.jpg",
        "signature_dish": "Malvani Fish Curry & Puran Poli",
        "lunch_price": 210.00,  # 160 + 50
        "dinner_price": 140.00, # 90 + 50
        "dietary": "NON_VEG",
        "rating": 4.8,
    },
    {
        "phone": "8693817484",
        "name": "Sumitra Dolas",
        "kitchen_name": "Dilse's Kitchen",
        "city": "Navi Mumbai",
        "cluster": "Ghansoli",
        "address": "Sector 8, Ghansoli, Navi Mumbai",
        "hometown": "Maharashtrian",
        "bio": "7 years experience preparing traditional Maharashtrian dishes, Thecha Chicken Thali, and fresh Ukdiche Modak.",
        "fssai": "FSSAI 21524089000305",
        "photo_url": "https://storage.tally.so/private/20250330_220621.jpg",
        "signature_dish": "Thecha Chicken Thali & Modak",
        "lunch_price": 170.00,  # 120 + 50
        "dinner_price": 140.00, # 90 + 50
        "dietary": "NON_VEG",
        "rating": 4.9,
    },
]


async def seed():
    async with SessionFactory() as db:
        for item in REAL_CHEFS_DATA:
            existing = await db.get(ChefProfile, item["phone"])
            if existing is None:
                chef = ChefProfile(
                    chef_phone=item["phone"],
                    chef_name=item["name"],
                    kitchen_name=item["kitchen_name"],
                    city=item["city"],
                    address=item["address"],
                    address_line1=item["address"],
                    apartment_or_locality=item["cluster"],
                    hometown_region=item["hometown"],
                    bio=item["bio"],
                    kitchen_bio=item["bio"],
                    fssai_license_number=item["fssai"],
                    payout_upi_id=f"{item['phone']}@upi",
                    profile_image_url=item["photo_url"],
                    latitude=Decimal("19.129300"),
                    longitude=Decimal("73.003400"),
                    active_status=True,
                    is_verified=True,
                )
                db.add(chef)
                logger.info(f"Inserted real chef: {item['kitchen_name']}")
            else:
                existing.chef_name = item["name"]
                existing.kitchen_name = item["kitchen_name"]
                existing.bio = item["bio"]
                existing.profile_image_url = item["photo_url"]
                existing.active_status = True
                existing.is_verified = True
                logger.info(f"Updated real chef: {item['kitchen_name']}")

        await db.commit()
        logger.info("Real chef profiles successfully seeded into PostgreSQL!")


if __name__ == "__main__":
    asyncio.run(seed())
