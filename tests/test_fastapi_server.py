"""Integration test suite for FastAPI Web Server & API Routers (Milestone 4)."""

import pytest
from httpx import ASGITransport, AsyncClient
from decimal import Decimal
from sqlalchemy.ext.asyncio import AsyncSession

from app.main import app
from app.models.chef import ChefProfile
from app.models.driver import DriverProfile


@pytest.mark.asyncio
async def test_health_check():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        res = await client.get("/health")
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "online"
        assert "Homaatri" in data["service"]



@pytest.mark.asyncio
async def test_verify_role_customer(db_session: AsyncSession):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        res = await client.post(
            "/api/v1/auth/verify_role",
            json={"phone": "9123456789", "requested_role": "CUSTOMER"},
        )
        assert res.status_code == 200
        data = res.json()
        assert data["authorized"] is True
        assert data["role"] == "CUSTOMER"


@pytest.mark.asyncio
async def test_verify_role_chef_unauthorized(db_session: AsyncSession):


    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        res = await client.post(
            "/api/v1/auth/verify_role",
            json={"phone": "9990000000", "requested_role": "CHEF"},
        )
        assert res.status_code == 200
        data = res.json()
        assert data["authorized"] is False
        assert "Access Denied" in data["message"]


@pytest.mark.asyncio
async def test_verify_role_chef_authorized(db_session: AsyncSession):
    session = db_session

    chef = ChefProfile(
        chef_phone="9876543210",
        kitchen_name="Indravati Tiffins",
        chef_name="Chef Sunita",
        address="Sector 4, Ghansoli",
        latitude=Decimal("19.1190086"),
        longitude=Decimal("72.9934054"),
    )
    session.add(chef)
    await session.commit()


    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        res = await client.post(
            "/api/v1/auth/verify_role",
            json={"phone": chef.chef_phone, "requested_role": "CHEF"},
        )
        assert res.status_code == 200
        data = res.json()
        assert data["authorized"] is True
        assert data["name"] == "Chef Sunita"
        assert data["details"]["kitchen_name"] == "Indravati Tiffins"


@pytest.mark.asyncio
async def test_verify_whatsapp_webhook_handshake():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        res = await client.get(
            "/api/v1/webhooks/whatsapp",
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": "homatri_verify_token_2026",
                "hub.challenge": "123456789_challenge",
            },
        )
        assert res.status_code == 200
        assert res.text == "123456789_challenge"
