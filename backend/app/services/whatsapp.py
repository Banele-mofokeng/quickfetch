import re
import httpx
from datetime import datetime
from typing import Optional

from sqlmodel import Session, select
from app.core.config import settings
from app.core.database import engine
from app.models.order import Order, OrderStatus, PaymentMethod
from app.models.driver import Driver, DriverStatus
from app.models.customer import Customer
from app.utils.maps import MapsService


class WhatsAppService:
    # Simple in-memory step state per customer phone. Use Redis in production.
    _state: dict[str, dict] = {}

    def __init__(self) -> None:
        self.maps = MapsService()

    # ===================== inbound =====================
    async def handle_webhook(self, payload: dict) -> None:
        event = payload.get("event")
        if event != "messages.upsert":
            return
        msg = payload.get("data", {})
        phone = msg.get("key", {}).get("remoteJid", "").replace("@s.whatsapp.net", "")
        if not phone:
            return
        body = msg.get("message", {})
        text = (body.get("conversation")
                or body.get("extendedTextMessage", {}).get("text") or "").strip()
        image = body.get("imageMessage")

        if await self._is_driver(phone):
            if image:
                await self._driver_receipt(phone, image)
            elif text:
                await self._driver_message(phone, text)
        elif text:
            await self._customer_message(phone, text)

    # ===================== customer =====================
    async def _customer_message(self, phone: str, text: str) -> None:
        low = text.lower()
        state = self._state.get(phone, {})

        if state.get("step") == "approve_cash":
            await self._handle_cash_approval(phone, low, state)
            return

        if low in ("hi", "hello", "start", "order") and not state.get("step"):
            self._state[phone] = {"step": "shop"}
            await self.send(phone, "👋 QuickFetch. Which shop should we buy from? "
                                   "Send the name and area, e.g. 'Debonairs, Fourways'.")
            return

        step = state.get("step")
        if step == "shop":
            state.update(step="items", shop=text)
            self._state[phone] = state
            await self.send(phone, f"What should we get from {text}? Include a budget, "
                                   f"e.g. 'Large pepperoni and a 2L Coke, budget R200'.")
        elif step == "items":
            budget = self._parse_amount(text) or 0.0
            state.update(step="address", items=text, budget=budget)
            self._state[phone] = state
            await self.send(phone, "📍 What's the delivery address?")
        elif step == "address":
            coords = await self.maps.geocode(text)
            if not coords:
                await self.send(phone, "I couldn't find that address. Please send a more "
                                       "specific address in Johannesburg.")
                return
            state.update(step="pay", address=text, coords=coords)
            self._state[phone] = state
            await self._present_payment_choice(phone, state)
        elif step == "pay":
            await self._handle_payment_choice(phone, low, state)

    async def _handle_cash_approval(self, phone: str, low: str, state: dict) -> None:
        order_id = state["order_id"]
        self._state.pop(phone, None)
        with Session(engine) as db:
            order = db.get(Order, order_id)
            if not order or order.status != OrderStatus.AWAITING_APPROVAL:
                return
            driver_phone = order.driver.phone if order.driver else None
            if low in ("yes", "y", "approve", "ok"):
                order.status = OrderStatus.AT_SHOP
                order.over_budget_approved = True
                order.touch()
                db.add(order)
                db.commit()
            else:
                order.status = OrderStatus.CANCELLED
                order.touch()
                db.add(order)
                if order.driver:
                    order.driver.status = DriverStatus.AVAILABLE
                    db.add(order.driver)
                db.commit()
        if low in ("yes", "y", "approve", "ok"):
            await self.send(phone, "✅ Approved. Your driver is completing the purchase.")
            if driver_phone:
                await self.send(driver_phone, "✅ Customer approved. Buy it, then reply "
                                              "'bought <amount>' with a photo of the slip.")
        else:
            await self.send(phone, "Order cancelled — you won't be charged.")
            if driver_phone:
                await self.send(driver_phone, "Customer declined. Order cancelled; you're free.")

    async def _present_payment_choice(self, phone: str, state: dict) -> None:
        budget = state["budget"]
        fee = settings.DELIVERY_FEE
        if budget <= settings.CASH_THRESHOLD:
            await self.send(
                phone,
                f"🧾 {state['shop']} — budget R{budget:.0f} + R{fee:.0f} delivery.\n"
                f"Reply *PAY* to pay now, or *CASH* to pay on delivery.")
        else:
            # Over the cash limit: prepay is the only option.
            order = await self._create_order(phone, state, PaymentMethod.PREPAY)
            from app.services.payment import PaymentService
            url = await PaymentService().create_budget_charge(order)
            await self.send(
                phone,
                f"🧾 {state['shop']} — budget R{budget:.0f} + R{fee:.0f} delivery = "
                f"R{budget + fee:.0f}.\nOrders over R{settings.CASH_THRESHOLD:.0f} are "
                f"prepaid. Pay here to confirm:\n{url}")
            self._state.pop(phone, None)

    async def _handle_payment_choice(self, phone: str, low: str, state: dict) -> None:
        if low == "cash":
            order = await self._create_order(phone, state, PaymentMethod.CASH)
            with Session(engine) as db:
                o = db.get(Order, order.id)
                o.status = OrderStatus.QUEUED  # cash is dispatchable immediately
                o.touch()
                db.add(o)
                db.commit()
            self._state.pop(phone, None)
            await self.send(phone, "✅ Order placed (cash on delivery). Finding you a driver…")
            from app.services.dispatch import DispatchService
            await DispatchService().dispatch(order.id)
        elif low in ("pay", "prepay"):
            order = await self._create_order(phone, state, PaymentMethod.PREPAY)
            from app.services.payment import PaymentService
            url = await PaymentService().create_budget_charge(order)
            self._state.pop(phone, None)
            await self.send(phone, f"Pay here to confirm your order:\n{url}")
        else:
            await self.send(phone, "Please reply *PAY* or *CASH*.")

    async def _create_order(self, phone: str, state: dict, method: PaymentMethod) -> Order:
        lat, lng = state["coords"]
        shop_coords = await self.maps.geocode(f"{state['shop']}, Johannesburg")
        with Session(engine) as db:
            if not db.exec(select(Customer).where(Customer.phone == phone)).first():
                db.add(Customer(phone=phone))
                db.commit()
            order = Order(
                customer_phone=phone,
                shop_name=state["shop"],
                shop_location=self.maps.point(*shop_coords) if shop_coords else None,
                request_description=state["items"],
                budget=int(round(state["budget"] * 100)),
                delivery_fee=int(round(settings.DELIVERY_FEE * 100)),
                delivery_address=state["address"],
                delivery_location=self.maps.point(lat, lng),
                payment_method=method,
                status=OrderStatus.PENDING_PAYMENT,
            )
            db.add(order)
            db.commit()
            db.refresh(order)
            return order

    # ===================== driver =====================
    async def _driver_message(self, phone: str, text: str) -> None:
        low = text.lower()
        if low in ("online", "available"):
            await self._set_driver(phone, DriverStatus.AVAILABLE,
                                   "🟢 You're online. We'll send jobs as they come in.")
        elif low == "offline":
            await self._set_driver(phone, DriverStatus.OFFLINE, "🔴 You're offline.")
        elif low == "accept":
            from app.services.dispatch import DispatchService
            await DispatchService().accept(phone)
        elif low == "decline":
            from app.services.dispatch import DispatchService
            await DispatchService().decline(phone)
        elif low in ("at shop", "arrived shop", "atshop"):
            await self._driver_at_shop(phone)
        elif low.startswith("bought"):
            await self._driver_bought(phone, self._parse_amount(text))
        elif low == "delivered":
            await self._driver_delivered(phone)

    async def _active_order(self, db: Session, phone: str,
                            statuses: tuple[OrderStatus, ...]) -> Optional[Order]:
        driver = db.exec(select(Driver).where(Driver.phone == phone)).first()
        if not driver:
            return None
        return db.exec(
            select(Order).where(Order.driver_id == driver.id)
            .where(Order.status.in_(statuses))
            .order_by(Order.created_at.desc())
        ).first()

    async def _driver_at_shop(self, phone: str) -> None:
        with Session(engine) as db:
            order = await self._active_order(
                db, phone, (OrderStatus.EN_ROUTE_TO_SHOP, OrderStatus.ASSIGNED))
            if not order:
                await self.send(phone, "No active pickup found.")
                return
            order.status = OrderStatus.AT_SHOP
            order.touch()
            db.add(order)
            db.commit()
            budget = order.budget_rands
            customer = order.customer_phone
        await self.send(phone, f"At the shop. Budget is R{budget:.0f}. When you have the "
                               f"total, reply 'bought <amount>' and send a photo of the slip.")
        await self.send(customer, "📍 Your driver is at the shop getting your order.")

    async def _driver_bought(self, phone: str, amount: Optional[float]) -> None:
        if amount is None:
            await self.send(phone, "Please include the amount, e.g. 'bought 185.50'.")
            return
        cents = int(round(amount * 100))
        with Session(engine) as db:
            order = await self._active_order(
                db, phone, (OrderStatus.AT_SHOP,))
            if not order:
                await self.send(phone, "No order awaiting purchase.")
                return
            customer = order.customer_phone
            budget = order.budget
            method = order.payment_method
            order_id = order.id
            approved = order.over_budget_approved
            if cents > budget and not approved:
                # Over budget — pause and get customer's go-ahead before buying.
                order.status = OrderStatus.AWAITING_APPROVAL
                order.actual_cost = cents
                order.touch()
                db.add(order)
                db.commit()
        if cents > budget and not approved:
            await self._raise_over_budget(order_id, method, customer, phone,
                                          extra=cents - budget, total=cents)
            return
        # Within budget — record purchase, head to customer.
        with Session(engine) as db:
            order = db.get(Order, order_id)
            order.actual_cost = cents
            order.status = OrderStatus.EN_ROUTE_TO_CUSTOMER
            order.purchased_at = datetime.utcnow()
            order.touch()
            db.add(order)
            db.commit()
            addr = order.delivery_address
        await self.send(phone, f"Got it — R{amount:.2f} recorded. Head to the customer:\n{addr}\n"
                               f"Reply 'delivered' when done.")
        await self.send(customer, "🛍️ Order collected. Your driver is on the way!")

    async def _raise_over_budget(self, order_id: int, method: PaymentMethod,
                                 customer: str, driver: str, extra: int, total: int) -> None:
        await self.send(driver, "That's over budget. Hold on — I'm checking with the customer.")
        if method == PaymentMethod.PREPAY:
            from app.services.payment import PaymentService
            with Session(engine) as db:
                order = db.get(Order, order_id)
            url = await PaymentService().create_topup_charge(order, extra)
            await self.send(
                customer,
                f"⚠️ Your order comes to R{total/100:.2f}, which is R{extra/100:.2f} over "
                f"your budget. Pay the extra R{extra/100:.2f} to continue:\n{url}\n"
                f"Or reply CANCEL.")
        else:  # CASH
            await self.send(
                customer,
                f"⚠️ Your order comes to R{total/100:.2f}, R{extra/100:.2f} over budget. "
                f"Reply YES to approve (you'll pay the higher amount on delivery) or NO to cancel.")
            self._state[customer] = {"step": "approve_cash", "order_id": order_id}

    async def _driver_delivered(self, phone: str) -> None:
        with Session(engine) as db:
            order = await self._active_order(
                db, phone, (OrderStatus.EN_ROUTE_TO_CUSTOMER, OrderStatus.ARRIVED))
            if not order:
                await self.send(phone, "No active delivery found.")
                return
            order.status = OrderStatus.DELIVERED
            order.delivered_at = datetime.utcnow()
            order.touch()
            driver = db.get(Driver, order.driver_id)
            if driver:
                driver.status = DriverStatus.AVAILABLE
                driver.total_deliveries += 1
                db.add(driver)
            db.add(order)
            db.commit()
            method = order.payment_method
            customer = order.customer_phone
            order_id = order.id
            actual = order.actual_rands or 0
            fee = order.fee_rands

        from app.services.payment import PaymentService
        pay = PaymentService()
        if method == PaymentMethod.CASH:
            pay.record_cash(await self._get_order(order_id))
            await self.send(customer, f"🎉 Delivered! Please pay the driver R{actual + fee:.2f} "
                                      f"(R{actual:.2f} goods + R{fee:.0f} delivery). Thank you!")
        else:
            refunded = await pay.refund_difference(await self._get_order(order_id))
            note = f" You'll get R{refunded/100:.2f} back." if refunded else ""
            await self.send(customer, f"🎉 Delivered!{note} Thanks for using QuickFetch.")
        with Session(engine) as db:
            o = db.get(Order, order_id)
            o.status = OrderStatus.COMPLETED
            o.touch()
            db.add(o)
            db.commit()
        await self.send(phone, "Delivery complete. You're available for the next job.")

    async def _driver_receipt(self, phone: str, image: dict) -> None:
        url = image.get("url") or image.get("mediaUrl") or "received"
        with Session(engine) as db:
            order = await self._active_order(
                db, phone, (OrderStatus.AT_SHOP, OrderStatus.EN_ROUTE_TO_CUSTOMER,
                            OrderStatus.AWAITING_APPROVAL))
            if not order:
                return
            order.receipt_url = url
            order.touch()
            db.add(order)
            db.commit()
        await self.send(phone, "📎 Slip received, thanks.")

    # ===================== helpers =====================
    async def _get_order(self, order_id: int) -> Order:
        with Session(engine) as db:
            return db.get(Order, order_id)

    async def _is_driver(self, phone: str) -> bool:
        with Session(engine) as db:
            return db.exec(select(Driver).where(Driver.phone == phone)).first() is not None

    async def _set_driver(self, phone: str, status: DriverStatus, reply: str) -> None:
        with Session(engine) as db:
            driver = db.exec(select(Driver).where(Driver.phone == phone)).first()
            if not driver:
                return
            driver.status = status
            driver.last_ping = datetime.utcnow()
            driver.touch()
            db.add(driver)
            db.commit()
        await self.send(phone, reply)

    @staticmethod
    def _parse_amount(text: str) -> Optional[float]:
        m = re.search(r"(\d+(?:\.\d{1,2})?)", text.replace("r", " ").replace("R", " "))
        return float(m.group(1)) if m else None

    async def send(self, phone: str, text: str) -> bool:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.post(
                    f"{settings.EVOLUTION_API_URL}/message/sendText/{settings.EVOLUTION_INSTANCE}",
                    headers={"apikey": settings.EVOLUTION_API_KEY},
                    json={"number": phone, "text": text},
                )
            return r.status_code in (200, 201)
        except Exception as exc:  # noqa: BLE001
            print(f"[wa] send failed to {phone}: {exc}")
            return False
