"""HTML pages: the 3-phone simulator and the mock checkout page."""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.db.session import SessionLocal
from app.services import order_lifecycle as lc

router = APIRouter(tags=["pages"])
templates = Jinja2Templates(directory="templates")


@router.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@router.get("/pay/{code}", response_class=HTMLResponse)
async def pay_page(request: Request, code: str):
    async with SessionLocal() as session:
        order = await lc.get_order_by_code(session, code)
        order_data = None
        if order:
            order_data = {
                "code": order.code,
                "customer_name": order.customer_name,
                "total": order.total,
                "due": order.balance_due,       # amount to pay NOW (delta for top-ups)
                "is_topup": order.balance_due < order.total,
                "subtotal": order.subtotal,
                "delivery_fee": order.delivery_fee,
                "status": order.status.value,
                "items": [
                    {"name": i.name, "quantity": i.quantity, "line_total": i.line_total}
                    for i in order.items
                ],
            }
    return templates.TemplateResponse(
        "pay.html", {"request": request, "order": order_data, "code": code}
    )
