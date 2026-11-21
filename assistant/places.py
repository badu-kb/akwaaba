"""Nearest health facilities to a shared WhatsApp location.

Uses OpenStreetMap (free, no key) through the Overpass API. Coverage of CHPS
compounds in Ghana is incomplete, so replies say where the list comes from and
always repeat the emergency numbers.
"""

import logging
import math

import httpx

log = logging.getLogger("akwaaba")

SERVERS = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter"]
QUERY = """[out:json][timeout:20];
(
  nwr["amenity"~"^(hospital|clinic|doctors)$"](around:{r},{lat},{lng});
  nwr["healthcare"~"^(hospital|clinic|centre|doctor|midwife|birthing_centre)$"](around:{r},{lat},{lng});
);
out tags center 60;"""
KIND = {"hospital": "Hospital", "clinic": "Clinic", "doctors": "Doctor", "centre": "Health centre",
        "doctor": "Doctor", "midwife": "Midwife", "birthing_centre": "Maternity"}


def distance_km(lat1, lng1, lat2, lng2) -> float:
    p = math.pi / 180
    a = (math.sin((lat2 - lat1) * p / 2) ** 2
         + math.cos(lat1 * p) * math.cos(lat2 * p) * math.sin((lng2 - lng1) * p / 2) ** 2)
    return 12742 * math.asin(math.sqrt(a))


def _query(lat, lng, radius, http) -> list[dict]:
    q = QUERY.format(r=radius, lat=lat, lng=lng)
    for url in SERVERS:
        try:
            resp = http.post(url, data={"data": q}, timeout=25,
                             headers={"User-Agent": "Akwaaba-WhatsApp-assistant/1.0"})
            if resp.status_code == 200:
                return resp.json().get("elements", [])
            log.warning("Facility search %s returned %s", url, resp.status_code)
        except (httpx.HTTPError, ValueError) as e:
            log.warning("Facility search failed at %s: %s", url, e)
    raise RuntimeError("facility search unavailable")


GEOCODER = "https://nominatim.openstreetmap.org/search"
COUNTRY = "gh"  # search town names inside Ghana first


def geocode(place: str, http=None) -> tuple[float, float, str] | None:
    """Town or area name -> (lat, lng, display name), using OpenStreetMap's Nominatim."""
    http = http or httpx.Client()
    for countries in (COUNTRY, None):
        params = {"q": place, "format": "json", "limit": 1, **({"countrycodes": countries} if countries else {})}
        try:
            resp = http.get(GEOCODER, params=params, timeout=15,
                            headers={"User-Agent": "Akwaaba-WhatsApp-assistant/1.0"})
            hits = resp.json() if resp.status_code == 200 else []
        except (httpx.HTTPError, ValueError) as e:
            log.warning("Place lookup failed: %s", e)
            raise RuntimeError("place search unavailable")
        if hits:
            h = hits[0]
            name = ", ".join(h.get("display_name", place).split(", ")[:2])
            return float(h["lat"]), float(h["lon"]), name
    return None


def nearest(lat: float, lng: float, n: int = 3, http=None) -> list[dict]:
    http = http or httpx.Client()
    found = {}
    for radius in (10_000, 40_000):
        for el in _query(lat, lng, radius, http):
            tags = el.get("tags") or {}
            name = tags.get("name") or tags.get("name:en")
            plat = el.get("lat") or (el.get("center") or {}).get("lat")
            plng = el.get("lon") or (el.get("center") or {}).get("lon")
            if not name or plat is None or plng is None:
                continue
            kind = KIND.get(tags.get("amenity")) or KIND.get(tags.get("healthcare")) or "Health facility"
            addr = ", ".join(x for x in (tags.get("addr:street"), tags.get("addr:city") or tags.get("addr:town")) if x)
            found[name.lower()] = {"name": name[:80], "kind": kind, "lat": float(plat), "lng": float(plng),
                                   "address": addr, "phone": tags.get("phone") or tags.get("contact:phone") or "",
                                   "km": distance_km(lat, lng, float(plat), float(plng))}
        if len(found) >= n:
            break
    return sorted(found.values(), key=lambda f: f["km"])[:n]
