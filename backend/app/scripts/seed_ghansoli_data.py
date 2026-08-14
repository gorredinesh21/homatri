"""Seed Script: Clean Database & Populate Ghansoli Sector 6 Home Chefs & Drivers.

Cleans existing table data and populates 4 authentic Home Chefs and 2 Delivery Drivers
centered around Ghansoli Sector 6, Navi Mumbai (Lat: 19.1235, Lng: 73.0012).
"""

from __future__ import annotations

import asyncio
from datetime import date
from decimal import Decimal
from sqlalchemy import text

from backend.app.db.session import engine, SessionFactory
from backend.app.models.chef import ChefDailyInventory, ChefMenuItem, ChefProfile

from backend.app.models.driver import DriverProfile


async def clean_and_seed_ghansoli_data():
    print("🛠️ Ensuring PostgreSQL tables are created...")
    import backend.app.models.chef  # noqa
    import backend.app.models.customer  # noqa
    import backend.app.models.driver  # noqa
    import backend.app.models.system  # noqa
    from backend.app.db.base import Base

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    print("🧹 Cleaning existing PostgreSQL table data...")
    async with SessionFactory() as session:
        # Truncate tables with CASCADE
        tables = [
            "conversation_messages",
            "system_payment_webhook_events",
            "driver_trip_status",
            "driver_routes",
            "customer_reviews",
            "customer_payments",
            "customer_order_items",
            "customer_orders",
            "chef_daily_inventory",
            "chef_menu_items",
            "chef_profiles",
            "driver_profiles",
            "customer_profiles",
        ]
        for t in tables:
            try:
                await session.execute(text(f"TRUNCATE TABLE {t} CASCADE;"))
            except Exception:
                pass
        await session.commit()
    print("✅ All database tables truncated cleanly!")


    print("\n👩‍🍳 Creating 4 Home Chef Profiles in Ghansoli Sector 6...")
    chefs_data = [
        {
            "chef_phone": "9876543210",
            "kitchen_name": "Indravati Pure Veg Tiffins",
            "chef_name": "Chef Sunita Sharma",
            "address": "Flat 402, Indravati CHS, Sector 6, Ghansoli, Navi Mumbai 400701",
            "city": "Navi Mumbai",
            "pincode": "400701",
            "latitude": Decimal("19.1240000"),
            "longitude": Decimal("73.0018000"),
            "dietary_type": "VEG",
            "kitchen_bio": "Authentic North Indian & Jain Ghar Ka Khana prepared with organic A2 cow ghee and zero palm oil.",
            "dishes": [
                {
                    "dish_name": "Jain Paneer Tikka Tiffin",
                    "description": "Soft paneer tikka cubes in rich tomato-cashew gravy (No Onion, No Garlic) + 4 Phulkas + Rice + Dal",
                    "price": Decimal("180.00"),
                    "dietary_type": "JAIN",
                    "meal_window": "LUNCH",
                },
                {
                    "dish_name": "Special Kathiyawadi Veg Thali",
                    "description": "Ringan Bharta, Sev Tamatar Subzi, 3 Bajra Rotla, Khichdi & Desi Ghee Chaas",
                    "price": Decimal("160.00"),
                    "dietary_type": "VEG",
                    "meal_window": "LUNCH",
                },
                {
                    "dish_name": "Whole Wheat Phulka Pack (4 Pcs)",
                    "description": "Freshly puffed whole wheat phulkas brushed with pure cow ghee",
                    "price": Decimal("40.00"),
                    "dietary_type": "VEG",
                    "meal_window": "LUNCH",
                },
                {
                    "dish_name": "Dal Tadka & Jeera Basmati Rice",
                    "description": "Yellow arhar dal tempered with cumin, garlic & ghee served with fragrant Basmati rice",
                    "price": Decimal("140.00"),
                    "dietary_type": "VEG",
                    "meal_window": "DINNER",
                },
            ],
        },
        {
            "chef_phone": "9876543211",
            "kitchen_name": "Konkan Coastal Flavors",
            "chef_name": "Chef Ananya Naik",
            "address": "Row House 12, Sector 5, Ghansoli, Navi Mumbai 400701",
            "city": "Navi Mumbai",
            "pincode": "400701",
            "latitude": Decimal("19.1220000"),
            "longitude": Decimal("73.0005000"),
            "dietary_type": "NON_VEG",
            "kitchen_bio": "Traditional Malvani & Konkani home recipes passed down generations. Fresh catch cooked in stone-ground coconut masalas.",
            "dishes": [
                {
                    "dish_name": "Surmai Fish Curry Tiffin",
                    "description": "Fresh King Fish cooked in coconut-kokum masala gravy + 2 Vade + Steam Rice + Sol Kadi",
                    "price": Decimal("280.00"),
                    "dietary_type": "NON_VEG",
                    "meal_window": "LUNCH",
                },
                {
                    "dish_name": "Chicken Sukka & Neer Dosa Meal",
                    "description": "Dry roasted Malvani chicken sukka with 4 soft rice neer dosas & coconut chutney",
                    "price": Decimal("240.00"),
                    "dietary_type": "NON_VEG",
                    "meal_window": "DINNER",
                },
                {
                    "dish_name": "Fresh Sol Kadi Bottle (250ml)",
                    "description": "Refreshing Konkan drink made from fresh coconut milk, kokum juice, garlic & green chillies",
                    "price": Decimal("50.00"),
                    "dietary_type": "VEG",
                    "meal_window": "LUNCH",
                },
                {
                    "dish_name": "Kala Chana Usal & Amboli Tiffin",
                    "description": "Spicy Malvani black gram curry served with 3 fermented rice-dal ambolis",
                    "price": Decimal("150.00"),
                    "dietary_type": "VEG",
                    "meal_window": "LUNCH",
                },
            ],
        },
        {
            "chef_phone": "9876543212",
            "kitchen_name": "Desi Punjabi Dhaba Tiffins",
            "chef_name": "Chef Rajesh Grewal",
            "address": "Shop 4, Palm Beach Arcade, Sector 4, Ghansoli, Navi Mumbai 400701",
            "city": "Navi Mumbai",
            "pincode": "400701",
            "latitude": Decimal("19.1205000"),
            "longitude": Decimal("72.9995000"),
            "dietary_type": "BOTH",
            "kitchen_bio": "Authentic Amritsari dhaba style comfort meals cooked in white butter and clay pots.",
            "dishes": [
                {
                    "dish_name": "Amritsari Chole Bhature Tiffin",
                    "description": "Slow-cooked black chickpeas in black tea spices + 2 fluffy bhature + pickled onion & mint chutney",
                    "price": Decimal("170.00"),
                    "dietary_type": "VEG",
                    "meal_window": "LUNCH",
                },
                {
                    "dish_name": "Tandoori Butter Chicken & Naan Combo",
                    "description": "Charcoal smoked boneless chicken in buttery makhani gravy + 2 garlic butter naans",
                    "price": Decimal("260.00"),
                    "dietary_type": "NON_VEG",
                    "meal_window": "DINNER",
                },
                {
                    "dish_name": "Sarson Ka Saag & Makki Roti Meal",
                    "description": "Traditional winter mustard greens topped with white butter + 2 maize rotis & jaggery",
                    "price": Decimal("190.00"),
                    "dietary_type": "VEG",
                    "meal_window": "LUNCH",
                },
                {
                    "dish_name": "Punjabi Malai Sweet Lassi (300ml)",
                    "description": "Thick chilled sweetened curd drink topped with fresh cream rabri",
                    "price": Decimal("60.00"),
                    "dietary_type": "VEG",
                    "meal_window": "LUNCH",
                },
            ],
        },
        {
            "chef_phone": "9876543213",
            "kitchen_name": "Dakshin Annapoorna Tiffins",
            "chef_name": "Chef Meenakshi Iyer",
            "address": "B-201, Tulsi Heights, Sector 7, Ghansoli, Navi Mumbai 400701",
            "city": "Navi Mumbai",
            "pincode": "400701",
            "latitude": Decimal("19.1260000"),
            "longitude": Decimal("73.0030000"),
            "dietary_type": "VEG",
            "kitchen_bio": "Traditional Tamil & South Indian home meals cooked with cold-pressed sesame oil and stone-ground spices.",
            "dishes": [
                {
                    "dish_name": "Mini Ghee Idli & Medu Vada Combo",
                    "description": "12 button idlis soaked in cow ghee & podi + 2 crispy medu vadas + coconut chutney & sambar",
                    "price": Decimal("130.00"),
                    "dietary_type": "VEG",
                    "meal_window": "LUNCH",
                },
                {
                    "dish_name": "Special Chettinad Veg Meals Tiffin",
                    "description": "Steamed Sona Masoori Rice + Drumstick Sambar + Pepper Rasam + Poriyal + Appalam + Payasam",
                    "price": Decimal("190.00"),
                    "dietary_type": "VEG",
                    "meal_window": "LUNCH",
                },
                {
                    "dish_name": "Mysore Masala Dosa Pack",
                    "description": "Crispy golden crepe smeared with spicy red chilli-garlic chutney & potato masala",
                    "price": Decimal("140.00"),
                    "dietary_type": "VEG",
                    "meal_window": "DINNER",
                },
                {
                    "dish_name": "Kumbakonam Degree Filter Coffee Flask",
                    "description": "Authentic chicory filter coffee brewed with thick fresh buffalo milk (Serves 2)",
                    "price": Decimal("70.00"),
                    "dietary_type": "VEG",
                    "meal_window": "LUNCH",
                },
            ],
        },
    ]

    async with SessionFactory() as session:
        today = date.today()
        for cd in chefs_data:
            dishes = cd.pop("dishes")
            chef_phone = cd["chef_phone"]

            chef_prof = ChefProfile(**cd)
            session.add(chef_prof)
            await session.flush()

            # Add Dishes & Daily Inventory
            for d in dishes:
                menu_item = ChefMenuItem(
                    chef_phone=chef_phone,
                    dish_name=d["dish_name"],
                    description=d["description"],
                    unit_price=d["price"],
                    dietary_tag=d["dietary_type"],
                    meal_type=d["meal_window"],
                    is_available=True,
                )
                session.add(menu_item)
                await session.flush()

                for mw in ["LUNCH", "DINNER"]:
                    inv = ChefDailyInventory(
                        chef_phone=chef_phone,
                        menu_item_id=menu_item.menu_item_id,
                        service_date=today,
                        meal_window=mw,
                        max_capacity=25,
                    )
                    session.add(inv)

        await session.commit()
    print("✅ 4 Home Chefs & Daily Menus Seeded Successfully!")


    print("\n🛵 Creating 2 Delivery Driver Profiles in Ghansoli Sector 6...")
    drivers_data = [
        {
            "driver_phone": "9111222333",
            "driver_name": "Vikram Solanki",
            "vehicle_type": "BIKE",
            "vehicle_number": "MH43AB1234",
            "vehicle_model": "Honda Activa 6G Black",
            "is_on_shift": True,
            "active_status": True,
        },
        {
            "driver_phone": "9111222334",
            "driver_name": "Amit Kumar",
            "vehicle_type": "BIKE",
            "vehicle_number": "MH43CD5678",
            "vehicle_model": "TVS Jupiter Grey",
            "is_on_shift": True,
            "active_status": True,
        },
    ]


    async with SessionFactory() as session:
        for dd in drivers_data:
            drv = DriverProfile(**dd)
            session.add(drv)
        await session.commit()
    print("✅ 2 Delivery Drivers Seeded Successfully!")

    print("\n🎉 ALL TABLES CLEANED & SEEDED FOR GHANSOLI SECTOR 6!")


if __name__ == "__main__":
    asyncio.run(clean_and_seed_ghansoli_data())
