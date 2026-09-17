"""Geographic helpers for the oil-spill workflow."""
from __future__ import annotations

import math
from typing import Iterable, Sequence


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def pixel_size_m(bbox: Sequence[float], width: int, height: int) -> tuple[float, float]:
    west, south, east, north = bbox
    lat_m = haversine_km(south, (west + east) / 2, north, (west + east) / 2) * 1000.0 / max(height, 1)
    lng_m = haversine_km((south + north) / 2, west, (south + north) / 2, east) * 1000.0 / max(width, 1)
    return lat_m, lng_m


def pixel_to_latlng(x: float, y: float, bbox: Sequence[float], width: int, height: int) -> tuple[float, float]:
    west, south, east, north = bbox
    lon = west + (x / max(width - 1, 1)) * (east - west)
    lat = north - (y / max(height - 1, 1)) * (north - south)
    return lat, lon


def bbox_center(bbox: Sequence[float]) -> tuple[float, float]:
    west, south, east, north = bbox
    return (south + north) / 2.0, (west + east) / 2.0


def pad_bbox(bbox: Sequence[float], pad_deg: float) -> list[float]:
    west, south, east, north = bbox
    return [west - pad_deg, max(-90.0, south - pad_deg), east + pad_deg, min(90.0, north + pad_deg)]


def split_bbox_grid(bbox: Sequence[float], rows: int, cols: int) -> list[list[float]]:
    west, south, east, north = bbox
    cells = []
    dw = (east - west) / cols
    dh = (north - south) / rows
    for r in range(rows):
        for c in range(cols):
            cells.append([
                west + c * dw,
                south + r * dh,
                west + (c + 1) * dw,
                south + (r + 1) * dh,
            ])
    return cells


def contour_to_polygon(ys: Iterable[float], xs: Iterable[float], bbox: Sequence[float], width: int, height: int) -> list[list[float]]:
    pts = []
    for y, x in zip(ys, xs):
        lat, lng = pixel_to_latlng(float(x), float(y), bbox, width, height)
        pts.append([lat, lng])
    if pts and pts[0] != pts[-1]:
        pts.append(pts[0])
    return pts


def destination_point(lat: float, lng: float, east_m: float, north_m: float) -> tuple[float, float]:
    dlat = north_m / 111_000.0
    clat = math.cos(math.radians(lat)) or 1e-6
    dlng = east_m / (111_000.0 * clat)
    return lat + dlat, lng + dlng


def bearing_deg(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lng2 - lng1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0
