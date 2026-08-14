import asyncio
import backend.app.models  # Registers all 24 models on Base.metadata
from backend.app.db.session import engine
from backend.app.db.base import Base

async def init_db():
    print("Creating all 24 Homaatri database tables in live PostgreSQL...")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("✅ ALL 24 TABLES SUCCESSFULLY CREATED IN POSTGRESQL!")

if __name__ == "__main__":
    asyncio.run(init_db())
