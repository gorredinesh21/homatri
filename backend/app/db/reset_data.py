"""System Reset Utility Script for Homatri Engine."""

import asyncio
import logging
from sqlalchemy import text
from backend.app.db.session import engine

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("homatri_reset")

async def reset_database():
    print("====================================================")
    print("🧹 STARTING COMPLETE HOMATRI DATABASE RESET...")
    print("====================================================")

    async with engine.begin() as conn:
        tables_to_truncate = [
            "customer_orders",
            "customer_order_items",
            "customer_addresses",
            "customer_profiles",
            "user_sessions",
        ]
        
        for table in tables_to_truncate:
            try:
                await conn.execute(text(f"TRUNCATE TABLE {table} RESTART IDENTITY CASCADE;"))
                print(f"  ✓ Truncated table: {table}")
            except Exception as e:
                print(f"  Notice on table {table}: {e}")

    print("====================================================")
    print("🟢 HOMATRI DATABASE RESET COMPLETED SUCCESSFULLY!")
    print("====================================================")

if __name__ == "__main__":
    asyncio.run(reset_database())
