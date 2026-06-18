import httpx
from typing import Optional, Tuple

# Rough greater-Johannesburg bounding box for sanity-checking addresses.
JHB_BOUNDS = {"north": -25.6, "south": -26.5, "east": 28.4, "west": 27.6}

_NOMINATIM = "https://nominatim.openstreetmap.org/search"
_OSRM = "https://router.project-osrm.org/route/v1/driving"
_HEADERS = {"User-Agent": "QuickFetch/1.0 (mofokengbanele9@gmail.com)"}


class MapsService:
    def __init__(self) -> None:
        self._cache: dict[str, Tuple[float, float]] = {}

    async def geocode(self, address: str) -> Optional[Tuple[float, float]]:
        if not address:
            return None
        key = address.lower().strip()
        if key in self._cache:
            return self._cache[key]
        try:
            async with httpx.AsyncClient(timeout=10, headers=_HEADERS) as client:
                r = await client.get(_NOMINATIM, params={
                    "q": address,
                    "format": "json",
                    "limit": 1,
                    "countrycodes": "za",
                    "viewbox": "27.6,-26.5,28.4,-25.6",
                    "bounded": 0,
                })
            results = r.json()
            if results:
                coords = (float(results[0]["lat"]), float(results[0]["lon"]))
                self._cache[key] = coords
                return coords
        except Exception as exc:  # noqa: BLE001
            print(f"[maps] geocode failed for {address!r}: {exc}")
        return None

    async def eta_seconds(self, origin: str, destination: str) -> int:
        if not origin or not destination:
            return 900
        try:
            o = self._as_latlng(origin)
            d = self._as_latlng(destination)
            if not o or not d:
                return 900
            # OSRM expects lng,lat order
            o_ll = self._swap_latlng(o)
            d_ll = self._swap_latlng(d)
            async with httpx.AsyncClient(timeout=12, headers=_HEADERS) as client:
                r = await client.get(
                    f"{_OSRM}/{o_ll};{d_ll}",
                    params={"overview": "false"},
                )
            data = r.json()
            if data.get("code") == "Ok" and data.get("routes"):
                return int(data["routes"][0]["duration"])
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

    def _as_latlng(self, value: str) -> Optional[str]:
        parsed = self.parse_point(value)
        return f"{parsed[0]},{parsed[1]}" if parsed else None

    @staticmethod
    def _swap_latlng(latlng: str) -> str:
        lat, lng = latlng.split(",")
        return f"{lng},{lat}"

    @staticmethod
    def in_johannesburg(lat: float, lng: float) -> bool:
        return (JHB_BOUNDS["south"] <= lat <= JHB_BOUNDS["north"]
                and JHB_BOUNDS["west"] <= lng <= JHB_BOUNDS["east"])
