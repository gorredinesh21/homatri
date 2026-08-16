import asyncio
from decimal import Decimal
from backend.app.db.session import SessionFactory
from backend.app.models.chef import ChefProfile, ChefMenuItem
from backend.app.models.driver import DriverProfile
from sqlalchemy import select, delete

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

DRIVERS = [
    dict(phone="9111111111", name="Rider Ramesh", lat="19.1240", lng="73.0018"),
    dict(phone="9222222222", name="Rider Suresh", lat="19.1220", lng="73.0005"),
]

async def seed_prod():
    async with SessionFactory() as session:
        async with session.begin():
            await session.execute(delete(ChefMenuItem))
            await session.execute(delete(ChefProfile))
            await session.execute(delete(DriverProfile))

            for c in CHEFS:
                cp = ChefProfile(
                    chef_phone=c['phone'],
                    kitchen_name=c['kitchen'],
                    chef_name=c['chef'],
                    address=c['addr'],
                    latitude=Decimal(c['lat']),
                    longitude=Decimal(c['lng']),
                    dietary_type=c['diet'],
                    active_status=True,
                )
                session.add(cp)
                await session.flush()

                for dish_name, price, window in c['dishes']:
                    item = ChefMenuItem(
                        chef_phone=c['phone'],
                        dish_name=dish_name,
                        unit_price=Decimal(price),
                        meal_type=window,
                        is_available=True,
                    )
                    session.add(item)

            for d in DRIVERS:
                dp = DriverProfile(
                    driver_phone=d['phone'],
                    driver_name=d['name'],
                    vehicle_number="MH-43-AZ-1234",
                    is_on_shift=True,
                    active_status=True,
                )
                session.add(dp)

    print("🟢 SEEDED CLOUD SQL POSTGRESQL: 4 Ghansoli Home Kitchens + 2 Drivers successfully!")

asyncio.run(seed_prod())
