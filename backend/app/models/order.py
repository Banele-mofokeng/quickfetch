from sqlmodel import SQLModel, Field, Relationship
from typing import Optional, TYPE_CHECKING, List
from datetime import datetime
from enum import Enum

if TYPE_CHECKING:
    from app.models.driver import Driver
    from app.models.payment import Payment


class PaymentMethod(str, Enum):
    PREPAY = "PREPAY"          # Budget secured via Paystack before dispatch
    CASH = "CASH"             # Cash on delivery (only allowed under threshold)


class OrderStatus(str, Enum):
    PENDING_PAYMENT = "PENDING_PAYMENT"        # prepay order awaiting budget payment
    QUEUED = "QUEUED"                          # ready to dispatch (cash starts here; prepay arrives here once paid)
    ASSIGNED = "ASSIGNED"                      # a driver has the job
    EN_ROUTE_TO_SHOP = "EN_ROUTE_TO_SHOP"
    AT_SHOP = "AT_SHOP"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"    # actual exceeds budget; waiting on customer
    PURCHASED = "PURCHASED"                    # goods bought, receipt captured
    EN_ROUTE_TO_CUSTOMER = "EN_ROUTE_TO_CUSTOMER"
    ARRIVED = "ARRIVED"
    DELIVERED = "DELIVERED"
    COMPLETED = "COMPLETED"                    # reconciled (prepay) or cash collected
    CANCELLED = "CANCELLED"


class Order(SQLModel, table=True):
    __tablename__ = "orders"

    id: Optional[int] = Field(default=None, primary_key=True)
    customer_phone: str = Field(index=True, max_length=20)

    # Where to buy from — free text only. The operator has no shop relationships.
    shop_name: str = Field(max_length=200)
    shop_location: Optional[str] = Field(default=None)   # "POINT(lng lat)"

    request_description: str = Field(max_length=1000)

    # Money is stored in cents to avoid float drift.
    budget: int = Field(default=0)                       # customer-authorised spend on goods
    delivery_fee: int = Field(default=0)                 # flat fee
    actual_cost: Optional[int] = Field(default=None)     # what the driver actually spent

    payment_method: PaymentMethod = Field(default=PaymentMethod.PREPAY)

    delivery_address: str = Field(max_length=500)
    delivery_location: Optional[str] = Field(default=None)

    status: OrderStatus = Field(default=OrderStatus.PENDING_PAYMENT, index=True)

    driver_id: Optional[int] = Field(default=None, foreign_key="drivers.id")

    receipt_url: Optional[str] = Field(default=None)     # photo of the till slip
    over_budget_approved: bool = Field(default=False)    # customer okayed spending over budget

    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    purchased_at: Optional[datetime] = Field(default=None)
    delivered_at: Optional[datetime] = Field(default=None)

    eta_to_shop: Optional[datetime] = Field(default=None)
    eta_delivery: Optional[datetime] = Field(default=None)

    driver: Optional["Driver"] = Relationship(back_populates="orders")
    payments: List["Payment"] = Relationship(back_populates="order")

    # --- money helpers (Rands) ---
    @property
    def budget_rands(self) -> float:
        return self.budget / 100

    @property
    def actual_rands(self) -> Optional[float]:
        return self.actual_cost / 100 if self.actual_cost is not None else None

    @property
    def fee_rands(self) -> float:
        return self.delivery_fee / 100

    @property
    def amount_to_charge(self) -> int:
        """Total the customer pays up-front on a prepay order (budget + fee)."""
        return self.budget + self.delivery_fee

    @property
    def is_over_budget(self) -> bool:
        return self.actual_cost is not None and self.actual_cost > self.budget

    @property
    def refund_due(self) -> int:
        """Cents to refund a prepay customer when they spent under budget."""
        if self.payment_method != PaymentMethod.PREPAY or self.actual_cost is None:
            return 0
        return max(self.budget - self.actual_cost, 0)

    def touch(self) -> None:
        self.updated_at = datetime.utcnow()
