"""Stage 5: map a confirmed slick to ship vs offshore platform using AIS."""
from __future__ import annotations

import math
from typing import Optional

from .geo import destination_point, haversine_km

# Approximate production assets inside each operational theater.
PLATFORMS = [
    {"id": "MUMBAI_HIGH", "name": "Mumbai High Field", "lat": 19.42, "lng": 71.33, "coast": "ARABIAN_SEA"},
    {"id": "MUMBAI_HIGH_S", "name": "Mumbai High South", "lat": 19.18, "lng": 71.42, "coast": "ARABIAN_SEA"},
    {"id": "FPSO_WEST", "name": "Western Offshore FPSO cluster", "lat": 18.55, "lng": 71.15, "coast": "ARABIAN_SEA"},
    {"id": "GOM_SHELF", "name": "Gulf of Mexico shelf platforms", "lat": 28.10, "lng": -93.20, "coast": "GULF_OF_MEXICO"},
    {"id": "GOM_GREEN_CANYON", "name": "Green Canyon / Walker Ridge area", "lat": 27.40, "lng": -90.80, "coast": "GULF_OF_MEXICO"},
    {"id": "JURONG", "name": "Jurong Island / Singapore refining approaches", "lat": 1.27, "lng": 103.72, "coast": "MALACCA_STRAIT"},
    {"id": "MALACCA_LANE", "name": "Malacca Strait tanker lane", "lat": 2.20, "lng": 101.90, "coast": "MALACCA_STRAIT"},
    {"id": "UPPER_ZAKUM", "name": "Upper Zakum field", "lat": 24.85, "lng": 53.65, "coast": "PERSIAN_GULF"},
    {"id": "HORMUZ", "name": "Strait of Hormuz approaches", "lat": 26.45, "lng": 56.30, "coast": "PERSIAN_GULF"},
    {"id": "FORTIES", "name": "Forties field", "lat": 57.73, "lng": 0.90, "coast": "NORTH_SEA"},
    {"id": "EKOFISK", "name": "Ekofisk complex", "lat": 56.55, "lng": 3.22, "coast": "NORTH_SEA"},
]


def _uv_from_met_dir(speed_ms: float, from_deg: float) -> tuple[float, float]:
    rad = math.radians(from_deg)
    u = -speed_ms * math.sin(rad)
    v = -speed_ms * math.cos(rad)
    return u, v


def _uv_to_dir(speed_ms: float, to_deg: float) -> tuple[float, float]:
    rad = math.radians(to_deg)
    u = speed_ms * math.sin(rad)
    v = speed_ms * math.cos(rad)
    return u, v


def backtrack_origin(lat: float, lng: float, metocean: dict, hours: float = 12.0) -> tuple[float, float]:
    wind_ms = metocean.get("windMs") or 0.0
    wind_dir = metocean.get("windDirDeg") or 0.0
    curr_ms = metocean.get("currentMs") or 0.0
    curr_dir = metocean.get("currentDirDeg") or 0.0
    u_w, v_w = _uv_from_met_dir(float(wind_ms), float(wind_dir))
    u_c, v_c = _uv_to_dir(float(curr_ms), float(curr_dir))
    u = u_c + 0.03 * u_w
    v = v_c + 0.03 * v_w
    east_m = -u * hours * 3600.0
    north_m = -v * hours * 3600.0
    return destination_point(lat, lng, east_m, north_m)


def _type_rank(vessel: dict) -> tuple[int, str]:
    raw = str(vessel.get("type") or vessel.get("category") or "").upper()
    name = str(vessel.get("name") or "").upper()
    try:
        code = int(raw)
    except ValueError:
        code = -1
    if (
        80 <= code <= 89
        or "TANKER" in raw
        or "TANKER" in name
        or "PETRO" in name
        or "FPSO" in name
        or "OIL" in name
    ):
        return 1, "tanker"
    if "RIG" in raw or "RIG" in name or "PLATFORM" in name or "FPSO" in raw or code >= 90:
        return 1, "platform_ais"
    if 70 <= code <= 79 or "CARGO" in raw or "CONTAINER" in raw:
        return 2, "cargo"
    if code == 30 or "FISH" in raw:
        return 3, "fishing"
    return 4, "other"


def attribute_source(candidate: dict, metocean: dict, vessels: list, coast_id: str) -> dict:
    lat, lng = candidate["lat"], candidate["lng"]
    origin_lat, origin_lng = backtrack_origin(lat, lng, metocean, hours=12.0)

    best = None
    for v in vessels or []:
        vlat = v.get("latitude", v.get("lat"))
        vlng = v.get("longitude", v.get("lng"))
        if vlat is None or vlng is None:
            continue
        d_now = haversine_km(lat, lng, float(vlat), float(vlng))
        d_origin = haversine_km(origin_lat, origin_lng, float(vlat), float(vlng))
        d = min(d_now, d_origin)
        if d > 40:
            continue
        rank, kind = _type_rank(v)
        sog = float(v.get("sog") or v.get("speed") or 0)
        # Prefer tankers / platforms; moving tankers near the drift corridor score highest.
        proximity = max(0.0, 1.0 - d / 40.0)
        type_w = {1: 1.0, 2: 0.72, 3: 0.45, 4: 0.35}[rank]
        moving_w = 1.0 if sog >= 0.5 else 0.85
        score = 100.0 * (0.55 * proximity + 0.45 * type_w) * moving_w
        rec = {
            "mmsi": str(v.get("mmsi") or ""),
            "name": v.get("name") or f"MMSI {v.get('mmsi')}",
            "type": kind,
            "aisType": v.get("type"),
            "sog": sog,
            "cog": v.get("cog") or v.get("course") or 0,
            "lat": float(vlat),
            "lng": float(vlng),
            "distanceKm": round(d_now, 2),
            "originDistanceKm": round(d_origin, 2),
            "score": round(score, 1),
        }
        if best is None or rec["score"] > best["score"]:
            best = rec

    platform_hit = None
    for p in PLATFORMS:
        if p["coast"] != coast_id:
            continue
        d = haversine_km(lat, lng, p["lat"], p["lng"])
        d0 = haversine_km(origin_lat, origin_lng, p["lat"], p["lng"])
        dist = min(d, d0)
        if dist <= 25:
            score = round(100.0 * max(0.0, 1.0 - dist / 25.0) * 0.9, 1)
            rec = {
                "mmsi": p["id"],
                "name": p["name"],
                "type": "offshore_platform",
                "aisType": "PLATFORM",
                "sog": 0,
                "cog": 0,
                "lat": p["lat"],
                "lng": p["lng"],
                "distanceKm": round(d, 2),
                "originDistanceKm": round(d0, 2),
                "score": score,
            }
            if platform_hit is None or rec["score"] > platform_hit["score"]:
                platform_hit = rec

    chosen = None
    cause = "unknown"
    if platform_hit and (best is None or platform_hit["score"] >= best["score"] - 5):
        # Stationary infrastructure wins when it is as close as the nearest ship.
        if best is None or platform_hit["distanceKm"] <= (best["distanceKm"] + 8) and (
            best is None or best["type"] != "tanker" or platform_hit["distanceKm"] < 12
        ):
            chosen = platform_hit
            cause = "offshore_platform"
    if chosen is None and best is not None:
        chosen = best
        cause = "vessel" if best["type"] in ("tanker", "cargo", "fishing", "other") else best["type"]
        if best["type"] == "platform_ais":
            cause = "offshore_platform"
    if chosen is None and platform_hit is not None:
        chosen = platform_hit
        cause = "offshore_platform"

    if chosen is None:
        return {
            "causeType": "unknown",
            "causeLabel": "No AIS vessel or platform within 40 km of slick / 12h updrift origin",
            "score": 28.0,
            "source": None,
            "origin": {"lat": round(origin_lat, 5), "lng": round(origin_lng, 5)},
        }

    label_map = {
        "offshore_platform": "offshore platform / FPSO",
        "vessel": f"ship ({chosen['type']})",
        "tanker": "ship (tanker)",
        "cargo": "ship (cargo)",
    }
    return {
        "causeType": cause,
        "causeLabel": label_map.get(cause, cause),
        "score": chosen["score"],
        "source": chosen,
        "origin": {"lat": round(origin_lat, 5), "lng": round(origin_lng, 5)},
    }
