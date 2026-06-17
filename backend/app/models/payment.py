from sqlmodel import SQLModel, Field, Relationship
from typing import Optional, TYPE_CHECKING
from datetime import datetime
from enum import Enum

if TYPE_CHECKING:
    from app.models.order import Order


class PaymentType(str, Enum):
    BUDGET = "BUDGET"        # initial prepay (budget + delivery fee)
    TOP_UP = "TOP_UP"       # extra charge when actual exceeds budget
    REFUND = "REFUND"       # money returned when spent under budget
    CASH = "CASH"          # cash collected on delivery (record only)


class PaymentStatus(str, Enum):
    PENDING = "PENDING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class Payment(SQLModel, table=True):
    __tablename__ = "payments"

    id: Optional[int] = Field(default=None, primary_key=True)
    order_id: int = Field(foreign_key="orders.id", index=True)

    reference: str = Field(unique=True, index=True, max_length=120)
    amount: int = Field()                       # cents (positive; REFUND means money out)
    type: PaymentType = Field(default=PaymentType.BUDGET)
    status: PaymentStatus = Field(default=PaymentStatus.PENDING, index=True)

    authorization_url: Optional[str] = Field(default=None)   # Paystack checkout link
    gateway_reference: Optional[str] = Field(default=None, max_length=120)

    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    paid_at: Optional[datetime] = Field(default=None)

    order: "Order" = Relationship(back_populates="payments")

    @property
    def amount_rands(self) -> float:
        return self.amount / 100

    @property
    def amount_kobo(self) -> int:
        # Paystack ZAR expects the minor unit (cents); our amount is already cents.
        return self.amount
