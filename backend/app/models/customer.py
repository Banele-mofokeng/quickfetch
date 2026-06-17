from sqlmodel import SQLModel, Field
from typing import Optional
from datetime import datetime


class Customer(SQLModel, table=True):
    __tablename__ = "customers"

    id: Optional[int] = Field(default=None, primary_key=True)
    phone: str = Field(unique=True, index=True, max_length=20)
    name: Optional[str] = Field(default=None, max_length=100)
    default_address: Optional[str] = Field(default=None, max_length=500)

    total_orders: int = Field(default=0)
    is_blocked: bool = Field(default=False)

    created_at: datetime = Field(default_factory=datetime.utcnow)
    last_order_at: Optional[datetime] = Field(default=None)
