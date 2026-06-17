from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, select
from typing import Optional, List

from app.core.database import get_session
from app.models.order import Order, OrderStatus
from app.models.driver import Driver, DriverStatus
from app.utils.maps import MapsService
from app.services.dispatch import DispatchService

router = APIRouter()
maps = MapsService()


def _serialize(order: Order, driver: Optional[Driver]) -> dict:
    d = None
    if driver:
        coords = maps.parse_point(driver.current_location)
        d = {"id": driver.id, "name": driver.name, "phone": driver.phone,
             "status": driver.status,
             "lat": coords[0] if coords else None, "lng": coords[1] if coords else None}
    return {
        "id": order.id, "customer_phone": order.customer_phone,
        "shop_name": order.shop_name, "items": order.request_description,
        "budget": order.budget_rands, "actual": order.actual_rands,
        "fee": order.fee_rands, "payment_method": order.payment_method,
        "status": order.status, "delivery_address": order.delivery_address,
        "receipt_url": order.receipt_url, "over_budget": order.is_over_budget,
        "created_at": order.created_at, "delivered_at": order.delivered_at,
        "eta_delivery": order.eta_delivery, "driver": d,
    }


@router.get("/", response_model=List[dict])
def list_orders(status: Optional[OrderStatus] = None, limit: int = Query(50, le=200),
                db: Session = Depends(get_session)):
    q = select(Order).order_by(Order.created_at.desc()).limit(limit)
    if status:
        q = q.where(Order.status == status)
    out = []
    for o in db.exec(q).all():
        out.append(_serialize(o, db.get(Driver, o.driver_id) if o.driver_id else None))
    return out


@router.get("/{order_id}")
def get_order(order_id: int, db: Session = Depends(get_session)):
    o = db.get(Order, order_id)
    if not o:
        raise HTTPException(404, "order not found")
    return _serialize(o, db.get(Driver, o.driver_id) if o.driver_id else None)


@router.post("/{order_id}/assign")
async def assign(order_id: int, driver_id: int, db: Session = Depends(get_session)):
    if not db.get(Order, order_id) or not db.get(Driver, driver_id):
        raise HTTPException(404, "order or driver not found")
    ok = await DispatchService().assign_manual(order_id, driver_id)
    return {"assigned": ok}


@router.post("/{order_id}/cancel")
def cancel(order_id: int, db: Session = Depends(get_session)):
    o = db.get(Order, order_id)
    if not o:
        raise HTTPException(404, "order not found")
    if o.status in (OrderStatus.DELIVERED, OrderStatus.COMPLETED, OrderStatus.CANCELLED):
        raise HTTPException(400, "order can't be cancelled")
    o.status = OrderStatus.CANCELLED
    o.touch()
    if o.driver_id:
        drv = db.get(Driver, o.driver_id)
        if drv:
            drv.status = DriverStatus.AVAILABLE
            db.add(drv)
    db.add(o)
    db.commit()
    return {"cancelled": True}
