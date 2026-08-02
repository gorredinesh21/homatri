"""Integration test suite for Razorpay Payment Gateway & Webhook Processing Engine."""

import pytest
from datetime import date
from decimal import Decimal
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.main import app
from app.models.chef import ChefProfile
from app.models.customer import CustomerOrder, CustomerProfile
from app.services.payment_service import razorpay_service
from app.tools.customer_tools import generate_payment_link_tool


@pytest.mark.asyncio
async def test_razorpay_service_mock_mode():
    res = await razorpay_service.create_payment_link(
        order_id="ord_test_999",
        amount_in_rupees=280.00,
        customer_phone="9123456789",
    )
    assert res["mode"] == "MOCK"
    assert "mock_payment.html" in res["short_url"]
    assert res["order_id"] == "ord_test_999"


@pytest.mark.asyncio
async def test_generate_payment_link_tool_integration(db_session: AsyncSession):
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

    cust = CustomerProfile(
        customer_phone="9123456789",
        name="Test Customer",
        delivery_address="Sector 4, Ghansoli",
        latitude=Decimal("19.1190086"),
        longitude=Decimal("72.9934054"),
        is_registered=True,
    )
    session.add(cust)
    await session.flush()

    order = CustomerOrder(
        order_id="ord_test_101",
        customer_phone="9123456789",
        chef_phone="9876543210",
        kitchen_name="Indravati Tiffins",
        service_date=date.fromisoformat("2026-08-02"),
        meal_window="LUNCH",
        total_amount=Decimal("250.00"),
        status="PENDING_PAYMENT",
    )
    session.add(order)
    await session.commit()

    res_str = await generate_payment_link_tool.ainvoke({
        "order_id": "ord_test_101",
        "customer_phone": "9123456789",
        "amount_due": 250.00,
    })
    assert "✅ Payment Link Generated Successfully" in res_str
    assert "ord_test_101" in res_str


@pytest.mark.asyncio
async def test_razorpay_webhook_router_paid_event(db_session: AsyncSession):
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

    cust = CustomerProfile(
        customer_phone="9123456789",
        name="Test Customer",
        delivery_address="Sector 4, Ghansoli",
        latitude=Decimal("19.1190086"),
        longitude=Decimal("72.9934054"),
        is_registered=True,
    )
    session.add(cust)
    await session.flush()

    order = CustomerOrder(
        order_id="ord_webhook_202",
        customer_phone="9123456789",
        chef_phone="9876543210",
        kitchen_name="Indravati Tiffins",
        service_date=date.fromisoformat("2026-08-02"),
        meal_window="LUNCH",
        total_amount=Decimal("320.00"),
        status="PENDING_PAYMENT",
    )
    session.add(order)
    await session.commit()

    payload = {
        "event": "payment_link.paid",
        "payload": {
            "payment_link": {
                "entity": {
                    "id": "plink_mock_test_202",
                    "order_id": "ord_webhook_202",
                    "amount": 32000,
                    "status": "paid",
                }
            },
            "payment": {
                "entity": {
                    "id": "pay_mock_test_txn_555",
                    "amount": 32000,
                    "currency": "INR",
                    "status": "captured",
                    "method": "upi",
                }
            },
        },
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        res = await client.post("/api/v1/webhooks/razorpay", json=payload)
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "SUCCESS"
        assert data["order_id"] == "ord_webhook_202"

    # Verify Order Status updated to CONFIRMED or PAID in DB via fresh session
    from app.db.session import SessionFactory
    async with SessionFactory() as verify_session:
        updated_order = await verify_session.get(CustomerOrder, "ord_webhook_202")
        assert updated_order is not None
        assert updated_order.status in {"CONFIRMED", "PAID"}







