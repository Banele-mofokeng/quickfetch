from fastapi import APIRouter, Depends
from sqlmodel import Session, select, func
from datetime import datetime, timedelta

from app.core.database import get_session
from app.core.config import settings
from app.models.order import Order, OrderStatus, PaymentMethod
from app.models.driver import Driver, DriverStatus

router = APIRouter()

ACTIVE = (OrderStatus.QUEUED, OrderStatus.ASSIGNED, OrderStatus.EN_ROUTE_TO_SHOP,
          OrderStatus.AT_SHOP, OrderStatus.AWAITING_APPROVAL, OrderStatus.PURCHASED,
          OrderStatus.EN_ROUTE_TO_CUSTOMER, OrderStatus.ARRIVED)


@router.get("/dashboard")
def dashboard(db: Session = Depends(get_session)):
    today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    cutoff = datetime.utcnow() - timedelta(minutes=5)

    completed_today = db.exec(
        select(func.count(Order.id)).where(Order.status == OrderStatus.COMPLETED)
        .where(Order.delivered_at >= today)).first() or 0
    revenue_today = db.exec(
        select(func.coalesce(func.sum(Order.delivery_fee), 0))
        .where(Order.status == OrderStatus.COMPLETED)
        .where(Order.delivered_at >= today)).first() or 0
    active = db.exec(select(func.count(Order.id)).where(Order.status.in_(ACTIVE))).first() or 0
    awaiting_pay = db.exec(select(func.count(Order.id))
                           .where(Order.status == OrderStatus.PENDING_PAYMENT)).first() or 0
    needs_assign = db.exec(select(func.count(Order.id))
                           .where(Order.status == OrderStatus.QUEUED)).first() or 0
    drivers_available = db.exec(
        select(func.count(Driver.id)).where(Driver.status == DriverStatus.AVAILABLE)
        .where(Driver.last_ping > cutoff)).first() or 0
    drivers_total = db.exec(select(func.count(Driver.id))
                            .where(Driver.is_active == True)).first() or 0  # noqa: E712

    return {
        "delivery_fee": settings.DELIVERY_FEE,
        "cash_threshold": settings.CASH_THRESHOLD,
        "completed_today": completed_today,
        "fee_revenue_today": revenue_today / 100,
        "active_orders": active,
        "awaiting_payment": awaiting_pay,
        "needs_assignment": needs_assign,
        "drivers_available": drivers_available,
        "drivers_total": drivers_total,
        "updated_at": datetime.utcnow(),
    }
