import httpx
from typing import Optional, Tuple
from app.core.config import settings

BASE = "https://maps.googleapis.com/maps/api"

# Rough greater-Johannesburg bounding box for sanity-checking addresses.
JHB_BOUNDS = {"north": -25.6, "south": -26.5, "east": 28.4, "west": 27.6}


class MapsService:
    def __init__(self) -> None:
        self.key = settings.GOOGLE_MAPS_API_KEY
        self._cache: dict[str, Tuple[float, float]] = {}

    async def geocode(self, address: str) -> Optional[Tuple[float, float]]:
        """Return (lat, lng) for an address, biased to South Africa, or None."""
        if not address or not self.key:
            return None
        key = address.lower().strip()
        if key in self._cache:
            return self._cache[key]
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(
                    f"{BASE}/geocode/json",
                    params={"address": address, "key": self.key,
                            "region": "za", "components": "country:ZA"},
                )
            data = r.json()
            if data.get("status") == "OK" and data["results"]:
                loc = data["results"][0]["geometry"]["location"]
                coords = (loc["lat"], loc["lng"])
                self._cache[key] = coords
                return coords
        except Exception as exc:  # noqa: BLE001
            print(f"[maps] geocode failed for {address!r}: {exc}")
        return None

    async def eta_seconds(self, origin: str, destination: str) -> int:
        """Travel time in seconds between two 'POINT(lng lat)' or address strings."""
        if not self.key or not origin or not destination:
            return 900
        try:
            async with httpx.AsyncClient(timeout=12) as client:
                r = await client.get(
                    f"{BASE}/directions/json",
                    params={"origin": self._as_latlng(origin),
                            "destination": self._as_latlng(destination),
                            "mode": "driving", "departure_time": "now",
                            "key": self.key},
                )
            data = r.json()
            if data.get("status") == "OK" and data["routes"]:
                leg = data["routes"][0]["legs"][0]
                return leg.get("duration_in_traffic", leg["duration"])["value"]
        except Exception as exc:  # noqa: BLE001
            print(f"[maps] eta failed: {exc}")
        return 900

    # --- PostGIS point helpers ---
    @staticmethod
    def point(lat: float, lng: float) -> str:
        return f"POINT({lng} {lat})"

    @staticmethod
    def parse_point(point: Optional[str]) -> Optional[Tuple[float, float]]:
        """'POINT(lng lat)' -> (lat, lng)."""
        if not point:
            return None
        try:
            lng, lat = map(float, point.replace("POINT(", "").rstrip(")").split())
            return (lat, lng)
        except Exception:  # noqa: BLE001
            return None

    def _as_latlng(self, value: str) -> str:
        parsed = self.parse_point(value)
        return f"{parsed[0]},{parsed[1]}" if parsed else value

    @staticmethod
    def in_johannesburg(lat: float, lng: float) -> bool:
        return (JHB_BOUNDS["south"] <= lat <= JHB_BOUNDS["north"]
                and JHB_BOUNDS["west"] <= lng <= JHB_BOUNDS["east"])
