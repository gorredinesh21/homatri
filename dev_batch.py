"""Batch / load-test fixtures for the /batch orchestrator.

Seeds 10 chefs (+ a signature dish in BOTH lunch & dinner, so orders work any time)
and 10 drivers. Provides the 60-customer roster (NOT seeded — the orchestrator
registers them via buttons). 60 customers spread 6-per-chef across all 10 chefs, so
every chef + driver is exercised. Change ROSTER_SIZE / the chef assignment to regroup.
"""

import os
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./poc.db")

import asyncio
from decimal import Decimal

import app.models  # noqa: F401  (register all models)
from app.db.base import Base
from app.db.session import SessionFactory, engine
from app.models.chef import ChefMenuItem, ChefProfile
from app.models.driver import DriverProfile
from app.models.system import SystemSetting

GH_LAT, GH_LNG = 19.1240, 73.0010   # Ghansoli Sector 6 centre
ROSTER_SIZE = 60

# 10 kitchens, each with one signature dish (seeded for LUNCH and DINNER).
CHEFS = [
    dict(phone="9900000001", kitchen="Indravati Pure Veg",    chef="Sunita Sharma",  diet="VEG",     dish="Paneer Butter Masala Thali", price="180"),
    dict(phone="9900000002", kitchen="Konkan Coastal",        chef="Ananya Naik",    diet="NON_VEG", dish="Surmai Fish Curry Meal",     price="280"),
    dict(phone="9900000003", kitchen="Desi Punjabi Dhaba",    chef="Rajesh Grewal",  diet="BOTH",    dish="Butter Chicken & Naan",      price="260"),
    dict(phone="9900000004", kitchen="Dakshin Annapoorna",    chef="Meenakshi Iyer", diet="VEG",     dish="Chettinad Veg Meals",        price="190"),
    dict(phone="9900000005", kitchen="Bengali Bhoj",          chef="Rupa Das",       diet="NON_VEG", dish="Kosha Mangsho & Rice",       price="240"),
    dict(phone="9900000006", kitchen="Gujarati Rasoi",        chef="Hetal Patel",    diet="VEG",     dish="Gujarati Thali",             price="170"),
    dict(phone="9900000007", kitchen="Hyderabadi House",      chef="Farida Begum",   diet="NON_VEG", dish="Chicken Dum Biryani",        price="250"),
    dict(phone="9900000008", kitchen="Malabar Kitchen",       chef="Thomas Kurian",  diet="NON_VEG", dish="Malabar Fish Meals",         price="270"),
    dict(phone="9900000009", kitchen="Rajasthani Rangat",     chef="Bhanu Singh",    diet="VEG",     dish="Dal Baati Churma Thali",     price="200"),
    dict(phone="9900000010", kitchen="South Tiffin Express",  chef="Latha Menon",    diet="VEG",     dish="Mini Tiffin Combo",          price="150"),
]

DRIVERS = [
    dict(phone=f"95000000{i:02d}", name=nm, vehicle=vn)
    for i, (nm, vn) in enumerate([
        ("Vikram Solanki", "MH43 AB 1201"), ("Amit Kumar", "MH43 CD 1202"),
        ("Suresh Yadav", "MH43 EF 1203"), ("Ganesh Pawar", "MH43 GH 1204"),
        ("Imran Shaikh", "MH43 IJ 1205"), ("Ravi Teja", "MH43 KL 1206"),
        ("Deepak Rao", "MH43 MN 1207"), ("Sandeep Jha", "MH43 OP 1208"),
        ("Manoj Gupta", "MH43 QR 1209"), ("Kiran More", "MH43 ST 1210"),
    ], start=1)
]

_FIRST = ["Aarav", "Vivaan", "Aditya", "Diya", "Ananya", "Ishaan", "Kabir", "Meera",
          "Riya", "Arjun", "Sara", "Rohan", "Neha", "Kunal", "Priya", "Dev",
          "Tara", "Yash", "Isha", "Nikhil"]
_TOWERS = ["Ashirwad", "Sunrise", "Green Meadows", "Lake View", "Palm Court", "Orchid"]


def build_roster(n: int = ROSTER_SIZE) -> list[dict]:
    """60 pre-scripted customers: phone, name, address, lat/lng (clustered), + a fixed order."""
    roster = []
    for i in range(n):
        chef = CHEFS[i % len(CHEFS)]                       # 6 customers per chef across all 10
        flat = 100 + i
        roster.append({
            "phone": f"80000000{i + 1:02d}",
            "name": f"{_FIRST[i % len(_FIRST)]} {chr(65 + (i % 26))}",
            "address": f"Flat {flat}, {_TOWERS[i % len(_TOWERS)]} CHS, Sector 6, Ghansoli",
            # small deterministic offsets so everyone is near the kitchens
            "lat": round(GH_LAT + ((i % 8) - 4) * 0.0006, 6),
            "lng": round(GH_LNG + ((i // 8) - 4) * 0.0006, 6),
            "kitchen": chef["kitchen"],
            "dish": chef["dish"],
            "qty": 1 + (i % 2),                            # 1 or 2
        })
    return roster


async def seed_batch() -> None:
    """Wipe everything and seed 10 chefs (+ dishes) + 10 drivers + delivery_fee. No customers."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    async with SessionFactory() as s:
        s.add(SystemSetting(key="delivery_fee", value={"amount": 20}, category="BUSINESS"))
        for k, c in enumerate(CHEFS):
            s.add(ChefProfile(
                chef_phone=c["phone"], kitchen_name=c["kitchen"], chef_name=c["chef"],
                address=f"Sector 6, Ghansoli (kitchen {k + 1})",
                latitude=Decimal(str(round(GH_LAT + ((k % 5) - 2) * 0.0008, 6))),
                longitude=Decimal(str(round(GH_LNG + ((k // 5) - 1) * 0.0008, 6))),
                dietary_type=c["diet"],
            ))
            for meal in ("LUNCH", "DINNER"):
                s.add(ChefMenuItem(
                    chef_phone=c["phone"], dish_name=c["dish"], unit_price=Decimal(c["price"]),
                    meal_type=meal, dietary_tag=c["diet"], spice_level="MEDIUM", is_available=True,
                ))
        for d in DRIVERS:
            s.add(DriverProfile(
                driver_phone=d["phone"], driver_name=d["name"], vehicle_type="BIKE",
                vehicle_number=d["vehicle"], vehicle_model="Activa", is_on_shift=True, active_status=True,
            ))
        await s.commit()
    print(f"Seeded batch: {len(CHEFS)} chefs (+lunch/dinner dishes) + {len(DRIVERS)} drivers. "
          f"Roster = {ROSTER_SIZE} customers (not seeded).")


if __name__ == "__main__":
    asyncio.run(seed_batch())
