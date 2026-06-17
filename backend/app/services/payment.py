import hmac
import hashlib
import json
import time
import httpx
from datetime import datetime
from typing import Optional

from sqlmodel import Session, select
from app.core.config import settings
from app.core.database import engine
from app.models.order import Order, OrderStatus, PaymentMethod
from app.models.payment import Payment, PaymentType, PaymentStatus

PAYSTACK = "https://api.paystack.co"


class PaymentService:
    def __init__(self) -> None:
        self.secret = settings.PAYSTACK_SECRET_KEY

    # ---------- creating charges ----------
    async def create_budget_charge(self, order: Order) -> str:
        """Up-front charge for a prepay order: goods budget + delivery fee."""
        return await self._init_charge(
            order, amount=order.amount_to_charge, ptype=PaymentType.BUDGET
        )

    async def create_topup_charge(self, order: Order, extra_cents: int) -> str:
        """Extra charge when the actual cost exceeds the secured budget."""
        return await self._init_charge(order, amount=extra_cents, ptype=PaymentType.TOP_UP)

    async def _init_charge(self, order: Order, amount: int, ptype: PaymentType) -> str:
        reference = f"qf_{order.id}_{ptype.value.lower()}_{int(time.time())}"
        email = f"cust_{order.customer_phone.lstrip('+')}@quickfetch.local"
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                f"{PAYSTACK}/transaction/initialize",
                headers={"Authorization": f"Bearer {self.secret}"},
                json={
                    "email": email,
                    "amount": amount,           # ZAR minor unit (cents)
                    "currency": "ZAR",
                    "reference": reference,
                    "metadata": {"order_id": order.id, "type": ptype.value},
                },
            )
        body = r.json()
        if not body.get("status"):
            raise RuntimeError(f"Paystack init failed: {body.get('message')}")
        url = body["data"]["authorization_url"]
        with Session(engine) as db:
            db.add(Payment(order_id=order.id, reference=reference, amount=amount,
                           type=ptype, status=PaymentStatus.PENDING, authorization_url=url))
            db.commit()
        return url

    # ---------- reconciliation ----------
    async def refund_difference(self, order: Order) -> Optional[int]:
        """Refund the unspent portion of a prepay budget. Returns cents refunded."""
        amount = order.refund_due
        if amount <= 0:
            return None
        reference = f"qf_{order.id}_refund_{int(time.time())}"
        # Find the original successful budget charge to refund against.
        with Session(engine) as db:
            budget_pay = db.exec(
                select(Payment).where(Payment.order_id == order.id)
                .where(Payment.type == PaymentType.BUDGET)
                .where(Payment.status == PaymentStatus.COMPLETED)
            ).first()
            db.add(Payment(order_id=order.id, reference=reference, amount=amount,
                           type=PaymentType.REFUND, status=PaymentStatus.PENDING))
            db.commit()
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                await client.post(
                    f"{PAYSTACK}/refund",
                    headers={"Authorization": f"Bearer {self.secret}"},
                    json={"transaction": budget_pay.gateway_reference if budget_pay else reference,
                          "amount": amount},
                )
        except Exception as exc:  # noqa: BLE001
            print(f"[pay] refund call failed (will still record intent): {exc}")
        with Session(engine) as db:
            pay = db.exec(select(Payment).where(Payment.reference == reference)).first()
            if pay:
                pay.status = PaymentStatus.COMPLETED
                pay.paid_at = datetime.utcnow()
                db.add(pay)
                db.commit()
        return amount

    def record_cash(self, order: Order) -> None:
        """Record cash collected on delivery (goods + fee). No gateway involved."""
        total = (order.actual_cost or 0) + order.delivery_fee
        with Session(engine) as db:
            db.add(Payment(order_id=order.id, reference=f"qf_{order.id}_cash_{int(time.time())}",
                           amount=total, type=PaymentType.CASH,
                           status=PaymentStatus.COMPLETED, paid_at=datetime.utcnow()))
            db.commit()

    # ---------- webhook ----------
    def verify_signature(self, body: bytes, signature: str) -> bool:
        expected = hmac.new(self.secret.encode(), body, hashlib.sha512).hexdigest()
        return hmac.compare_digest(expected, signature or "")

    async def handle_webhook(self, payload: dict) -> None:
        if payload.get("event") != "charge.success":
            return
        data = payload.get("data", {})
        reference = data.get("reference")
        if not reference:
            return
        with Session(engine) as db:
            pay = db.exec(select(Payment).where(Payment.reference == reference)).first()
            if not pay or pay.status == PaymentStatus.COMPLETED:
                return
            pay.status = PaymentStatus.COMPLETED
            pay.paid_at = datetime.utcnow()
            pay.gateway_reference = str(data.get("id") or "")
            order = db.get(Order, pay.order_id)
            ptype = pay.type
            db.add(pay)
            db.commit()
            order_id = order.id if order else None

        # React to the confirmed payment outside the first session.
        if ptype == PaymentType.BUDGET:
            await self._on_budget_paid(order_id)
        elif ptype == PaymentType.TOP_UP:
            await self._on_topup_paid(order_id)

    async def _on_budget_paid(self, order_id: Optional[int]) -> None:
        """Prepay secured -> make the order dispatchable and kick off dispatch."""
        if order_id is None:
            return
        from app.services.dispatch import DispatchService
        from app.services.whatsapp import WhatsAppService
        with Session(engine) as db:
            order = db.get(Order, order_id)
            if not order or order.status != OrderStatus.PENDING_PAYMENT:
                return
            order.status = OrderStatus.QUEUED
            order.touch()
            db.add(order)
            db.commit()
            phone = order.customer_phone
        await WhatsAppService().send(phone, "✅ Payment secured. Finding you a driver now…")
        await DispatchService().dispatch(order_id)

    async def _on_topup_paid(self, order_id: Optional[int]) -> None:
        """Over-budget top-up secured -> let the driver complete the purchase."""
        if order_id is None:
            return
        from app.services.whatsapp import WhatsAppService
        with Session(engine) as db:
            order = db.get(Order, order_id)
            if not order or order.status != OrderStatus.AWAITING_APPROVAL:
                return
            order.status = OrderStatus.AT_SHOP
            order.over_budget_approved = True
            order.touch()
            db.add(order)
            db.commit()
            driver_phone = order.driver.phone if order.driver else None
            customer_phone = order.customer_phone
        if driver_phone:
            await WhatsAppService().send(
                driver_phone,
                "✅ Customer approved the higher amount. Complete the purchase, then reply "
                "'bought <amount>' with a photo of the slip.")
        await WhatsAppService().send(customer_phone, "✅ Top-up received. Your driver is completing the purchase.")
