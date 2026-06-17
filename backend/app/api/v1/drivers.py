from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select
from typing import List
from datetime import datetime

from app.core.database import get_session
from app.models.driver import Driver, DriverStatus
from app.utils.maps import MapsService

router = APIRouter()
maps = MapsService()


@router.get("/", response_model=List[dict])
def list_drivers(db: Session = Depends(get_session)):
    out = []
    for d in db.exec(select(Driver).order_by(Driver.name)).all():
        coords = maps.parse_point(d.current_location)
        out.append({"id": d.id, "name": d.name, "phone": d.phone, "vehicle": d.vehicle,
                    "status": d.status, "is_active": d.is_active,
                    "available": d.is_available, "total_deliveries": d.total_deliveries,
                    "lat": coords[0] if coords else None,
                    "lng": coords[1] if coords else None, "last_ping": d.last_ping})
    return out


@router.post("/")
def create_driver(name: str, phone: str, vehicle: str = "bike",
                  db: Session = Depends(get_session)):
    if db.exec(select(Driver).where(Driver.phone == phone)).first():
        raise HTTPException(400, "driver with that phone already exists")
    driver = Driver(name=name, phone=phone, vehicle=vehicle)
    db.add(driver)
    db.commit()
    db.refresh(driver)
    return {"id": driver.id, "name": driver.name, "phone": driver.phone}


@router.post("/{driver_id}/location")
def ping_location(driver_id: int, lat: float, lng: float,
                  db: Session = Depends(get_session)):
    d = db.get(Driver, driver_id)
    if not d:
        raise HTTPException(404, "driver not found")
    d.set_location(lat, lng)
    db.add(d)
    db.commit()
    return {"ok": True}


@router.get("/{driver_id}")
def get_driver(driver_id: int, db: Session = Depends(get_session)):
    d = db.get(Driver, driver_id)
    if not d:
        raise HTTPException(404, "driver not found")
    coords = maps.parse_point(d.current_location)
    return {"id": d.id, "name": d.name, "phone": d.phone, "vehicle": d.vehicle,
            "status": d.status, "is_active": d.is_active, "available": d.is_available,
            "total_deliveries": d.total_deliveries,
            "lat": coords[0] if coords else None, "lng": coords[1] if coords else None}
