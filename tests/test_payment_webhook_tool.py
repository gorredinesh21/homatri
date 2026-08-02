"""Integration test suite for process_payment_gateway_webhook_tool."""

import pytest
from datetime import date
from decimal import Decimal
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chef import ChefProfile
from app.models.customer import CustomerOrder, CustomerPayment, CustomerProfile
from app.models.system import SystemOutboundQueue, SystemPaymentWebhookEvent
from app.tools.master_tools import process_payment_gateway_webhook


@pytest.mark.asyncio
async def test_process_payment_gateway_webhook_success(db_session: AsyncSession):
    session = db_session

    # 1. Seed Chef, Customer and CustomerOrder
    chef = ChefProfile(
        chef_phone="9876543210",
        kitchen_name="Cloud 36 Kitchen",
        chef_name="Chef Cloud",
        address="Cloud 36, Ghansoli",
        latitude=Decimal("19.1190086"),
        longitude=Decimal("72.9934054"),
    )
    cust = CustomerProfile(
        customer_phone="9123456789",
        name="Rajesh Test",
        delivery_address="Indravati CHS, Ghansoli",
        latitude=Decimal("19.1214684"),
        longitude=Decimal("73.0036295"),
    )
    session.add_all([chef, cust])
    await session.flush()


    order = CustomerOrder(
        order_id="ord_pay_test_01",
        customer_phone=cust.customer_phone,
        chef_phone="9876543210",
        kitchen_name="Cloud 36 Kitchen",

        meal_window="LUNCH",
        service_date=date(2026, 8, 2),
        status="PENDING_PAYMENT",
        cart_subtotal=Decimal("250.00"),
        delivery_fee=Decimal("30.00"),
        total_amount=Decimal("280.00"),
    )
    session.add(order)
    await session.flush()

    # 2. Process Mock Webhook Payment Event
    res = await process_payment_gateway_webhook(
        session,
        gateway_event_id="evt_mock_pay_999",
        event_type="MOCK_PAYMENT_SUCCESS",
        order_id=order.order_id,
        payment_id="pay_mock_12345",
        amount_paid=Decimal("280.00"),
        is_mock=True,
    )

    # 3. Verify Return Dict
    assert res is not None
    assert res["status"] == "SUCCESS"
    assert res["gateway"] == "MOCK_GATEWAY"
    assert res["order_status"] == "CONFIRMED"

    # 4. Verify CustomerOrder status transitioned to CONFIRMED (DW1 cascade)
    updated_order = await session.get(CustomerOrder, order.order_id)
    assert updated_order is not None
    assert updated_order.status == "CONFIRMED"

    # 5. Verify CustomerPayment record status is PAID (DW2 update)
    stmt_pay = select(CustomerPayment).where(CustomerPayment.order_id == order.order_id)
    updated_pay = (await session.execute(stmt_pay)).scalar_one_or_none()
    assert updated_pay is not None
    assert updated_pay.status == "PAID"
    assert updated_pay.amount_paid == Decimal("280.00")

    # 6. Verify SystemPaymentWebhookEvent recorded
    stmt_evt = select(SystemPaymentWebhookEvent).where(
        SystemPaymentWebhookEvent.gateway_event_id == "evt_mock_pay_999"
    )
    evt = (await session.execute(stmt_evt)).scalar_one_or_none()
    assert evt is not None
    assert evt.processing_status == "PROCESSED"

    # 7. Test Idempotency (Duplicate Event Ignored)
    dup_res = await process_payment_gateway_webhook(
        session,
        gateway_event_id="evt_mock_pay_999",
        event_type="MOCK_PAYMENT_SUCCESS",
        order_id=order.order_id,
        payment_id="pay_mock_12345",
        amount_paid=Decimal("280.00"),
        is_mock=True,
    )
    assert dup_res["status"] == "IDEMPOTENT_SKIPPED"


@pytest.mark.asyncio
async def test_process_payment_gateway_webhook_invalid_event_assertion(db_session: AsyncSession):
    session = db_session
    with pytest.raises(AssertionError, match="Unsupported event_type"):
        await process_payment_gateway_webhook(
            session,
            gateway_event_id="evt_fail",
            event_type="UNSUPPORTED_FOO_EVENT",
            order_id="ord_fail",
            amount_paid=Decimal("100.00"),
        )
