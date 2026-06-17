from sqlmodel import SQLModel, Field, Relationship
from typing import Optional, TYPE_CHECKING, List
from datetime import datetime, timedelta
from enum import Enum

if TYPE_CHECKING:
    from app.models.order import Order


class DriverStatus(str, Enum):
    OFFLINE = "OFFLINE"
    AVAILABLE = "AVAILABLE"
    ON_JOB = "ON_JOB"


class Driver(SQLModel, table=True):
    __tablename__ = "drivers"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(max_length=100)
    phone: str = Field(unique=True, index=True, max_length=20)
    vehicle: str = Field(default="bike", max_length=30)   # bike / car

    status: DriverStatus = Field(default=DriverStatus.OFFLINE, index=True)
    current_location: Optional[str] = Field(default=None)  # "POINT(lng lat)"
    last_ping: Optional[datetime] = Field(default=None, index=True)

    is_active: bool = Field(default=True, index=True)
    total_deliveries: int = Field(default=0)

    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    orders: List["Order"] = Relationship(back_populates="driver")

    @property
    def is_available(self) -> bool:
        return (
            self.is_active
            and self.status == DriverStatus.AVAILABLE
            and self.last_ping is not None
            and datetime.utcnow() - self.last_ping < timedelta(minutes=5)
        )

    def set_location(self, lat: float, lng: float) -> None:
        self.current_location = f"POINT({lng} {lat})"
        self.last_ping = datetime.utcnow()
        self.updated_at = datetime.utcnow()

    def touch(self) -> None:
        self.updated_at = datetime.utcnow()
