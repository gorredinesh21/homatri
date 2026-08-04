"""DEV reset (SQLite poc.db): fresh DB with the 4 Ghansoli chefs + system_settings,
and NO customer — so you can test Flow 1 (registration) from the very start.

Run:  python dev_reset.py
"""

import os
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./poc.db"

import asyncio
from decimal import Decimal

import app.models  # noqa: F401  (register all models)
from app.db.base import Base
from app.db.session import SessionFactory, engine
from app.models.chef import ChefMenuItem, ChefProfile
from app.models.driver import DriverProfile
from app.models.system import SystemSetting

# 4 real Ghansoli chefs, each with LUNCH + DINNER dishes (same as dev_seed).
CHEFS = [
    dict(phone="9876543210", kitchen="Indravati Pure Veg Tiffins", chef="Chef Sunita Sharma",
         addr="Indravati CHS, Sector 6, Ghansoli", lat="19.1240", lng="73.0018", diet="VEG",
         dishes=[("Jain Paneer Tikka Tiffin", "180.00", "LUNCH"), ("Dal Tadka & Jeera Rice", "140.00", "DINNER")]),
    dict(phone="9876543211", kitchen="Konkan Coastal Flavors", chef="Chef Ananya Naik",
         addr="Sector 5, Ghansoli", lat="19.1220", lng="73.0005", diet="NON_VEG",
         dishes=[("Surmai Fish Curry Tiffin", "280.00", "LUNCH"), ("Chicken Sukka & Neer Dosa", "240.00", "DINNER")]),
    dict(phone="9876543212", kitchen="Desi Punjabi Dhaba Tiffins", chef="Chef Rajesh Grewal",
         addr="Sector 4, Ghansoli", lat="19.1205", lng="72.9995", diet="BOTH",
         dishes=[("Amritsari Chole Bhature Tiffin", "170.00", "LUNCH"), ("Butter Chicken & Naan", "260.00", "DINNER")]),
    dict(phone="9876543213", kitchen="Dakshin Annapoorna Tiffins", chef="Chef Meenakshi Iyer",
         addr="Sector 7, Ghansoli", lat="19.1260", lng="73.0030", diet="VEG",
         dishes=[("Special Chettinad Veg Meals", "190.00", "LUNCH"), ("Mysore Masala Dosa Pack", "140.00", "DINNER")]),
]

# 2 Ghansoli drivers, both on shift (from the old seed data).
DRIVERS = [
    dict(phone="9111222333", name="Vikram Solanki", vehicle_type="BIKE", vehicle_number="MH43 AB 1234", model="Honda Activa 6G"),
    dict(phone="9111222334", name="Amit Kumar", vehicle_type="BIKE", vehicle_number="MH43 CD 5678", model="TVS Jupiter"),
]


async def main():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)      # wipes ALL data incl. customers
        await conn.run_sync(Base.metadata.create_all)

    async with SessionFactory() as s:
        s.add(SystemSetting(key="delivery_fee", value={"amount": 20}, category="BUSINESS"))
        for c in CHEFS:
            s.add(ChefProfile(
                chef_phone=c["phone"], kitchen_name=c["kitchen"], chef_name=c["chef"],
                address=c["addr"], latitude=Decimal(c["lat"]), longitude=Decimal(c["lng"]),
                dietary_type=c["diet"],
            ))
            for dish_name, price, meal in c["dishes"]:
                s.add(ChefMenuItem(
                    chef_phone=c["phone"], dish_name=dish_name, unit_price=Decimal(price),
                    meal_type=meal, dietary_tag=c["diet"], is_available=True,
                ))
        for d in DRIVERS:
            s.add(DriverProfile(
                driver_phone=d["phone"], driver_name=d["name"], vehicle_type=d["vehicle_type"],
                vehicle_number=d["vehicle_number"], vehicle_model=d["model"],
                is_on_shift=True, active_status=True,
            ))
        await s.commit()

    print("Reset poc.db: 4 Ghansoli chefs + 2 drivers + delivery_fee=20. NO customer — register from scratch.")


if __name__ == "__main__":
    asyncio.run(main())
