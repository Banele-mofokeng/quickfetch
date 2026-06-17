from datetime import datetime, timedelta
from typing import Optional

from sqlmodel import Session, select, text
from app.core.database import engine
from app.models.order import Order, OrderStatus
from app.models.driver import Driver, DriverStatus


class DispatchService:
    async def dispatch(self, order_id: int, exclude_driver_id: Optional[int] = None) -> Optional[int]:
        """Assign the nearest available driver to a queued order. Returns driver id or None."""
        from app.services.whatsapp import WhatsAppService
        wa = WhatsAppService()
        with Session(engine) as db:
            order = db.get(Order, order_id)
            if not order or order.status != OrderStatus.QUEUED:
                return None
            driver = self._nearest_available(db, order, exclude_driver_id)
            if not driver:
                # No one free: leave it QUEUED for the operator to assign by hand.
                customer = order.customer_phone
                db.commit()
                await wa.send(customer, "All drivers are busy right now — we'll assign one "
                                        "shortly and keep you posted.")
                return None
            order.driver_id = driver.id
            order.status = OrderStatus.ASSIGNED
            order.touch()
            driver.status = DriverStatus.ON_JOB
            db.add(order)
            db.add(driver)
            db.commit()
            driver_phone, customer = driver.phone, order.customer_phone
            summary = (f"🛵 New job #{order.id}\n"
                       f"Shop: {order.shop_name}\n"
                       f"Items: {order.request_description}\n"
                       f"Budget: R{order.budget_rands:.0f} ({order.payment_method.value})\n"
                       f"Deliver to: {order.delivery_address}\n"
                       f"Reply 'accept' or 'decline'.")
        await wa.send(driver_phone, summary)
        await wa.send(customer, "🛵 A driver has been assigned and will confirm shortly.")
        return driver.id

    async def accept(self, driver_phone: str) -> bool:
        from app.services.whatsapp import WhatsAppService
        wa = WhatsAppService()
        with Session(engine) as db:
            driver = db.exec(select(Driver).where(Driver.phone == driver_phone)).first()
            if not driver:
                return False
            order = db.exec(
                select(Order).where(Order.driver_id == driver.id)
                .where(Order.status == OrderStatus.ASSIGNED)).first()
            if not order:
                await wa.send(driver_phone, "No job to accept.")
                return False
            order.status = OrderStatus.EN_ROUTE_TO_SHOP
            order.touch()
            db.add(order)
            db.commit()
            shop, customer = order.shop_name, order.customer_phone
        await wa.send(driver_phone, f"✅ Accepted. Head to {shop}. Reply 'at shop' on arrival.")
        await wa.send(customer, "✅ Your driver is on the way to the shop.")
        return True

    async def decline(self, driver_phone: str) -> bool:
        from app.services.whatsapp import WhatsAppService
        wa = WhatsAppService()
        with Session(engine) as db:
            driver = db.exec(select(Driver).where(Driver.phone == driver_phone)).first()
            if not driver:
                return False
            order = db.exec(
                select(Order).where(Order.driver_id == driver.id)
                .where(Order.status == OrderStatus.ASSIGNED)).first()
            if not order:
                return False
            order.driver_id = None
            order.status = OrderStatus.QUEUED
            order.touch()
            driver.status = DriverStatus.AVAILABLE
            db.add(order)
            db.add(driver)
            db.commit()
            order_id, declined = order.id, driver.id
        await wa.send(driver_phone, "No problem — finding someone else.")
        await self.dispatch(order_id, exclude_driver_id=declined)
        return True

    async def assign_manual(self, order_id: int, driver_id: int) -> bool:
        """Operator assigns a specific driver from the dashboard."""
        from app.services.whatsapp import WhatsAppService
        wa = WhatsAppService()
        with Session(engine) as db:
            order = db.get(Order, order_id)
            driver = db.get(Driver, driver_id)
            if not order or not driver:
                return False
            order.driver_id = driver.id
            order.status = OrderStatus.ASSIGNED
            order.touch()
            driver.status = DriverStatus.ON_JOB
            db.add(order)
            db.add(driver)
            db.commit()
            phone = driver.phone
            summary = (f"🛵 New job #{order.id}\nShop: {order.shop_name}\n"
                       f"Items: {order.request_description}\nReply 'accept' or 'decline'.")
        await wa.send(phone, summary)
        return True

    def _nearest_available(self, db: Session, order: Order,
                           exclude: Optional[int]) -> Optional[Driver]:
        cutoff = datetime.utcnow() - timedelta(minutes=5)
        drivers = db.exec(
            select(Driver).where(Driver.status == DriverStatus.AVAILABLE)
            .where(Driver.is_active == True)  # noqa: E712
            .where(Driver.last_ping > cutoff)
        ).all()
        drivers = [d for d in drivers if d.id != exclude]
        if not drivers:
            return None
        if not order.shop_location:
            return drivers[0]
        # Rank by PostGIS distance to the shop.
        best, best_dist = None, float("inf")
        for d in drivers:
            if not d.current_location:
                continue
            dist = db.exec(
                text("SELECT ST_Distance(ST_GeogFromText(:a), ST_GeogFromText(:b))"),
                {"a": d.current_location, "b": order.shop_location},
            ).first()
            dist = dist[0] if dist else float("inf")
            if dist < best_dist:
                best, best_dist = d, dist
        return best or drivers[0]
