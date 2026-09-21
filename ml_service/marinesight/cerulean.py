"""SkyTruth Cerulean public OGC API client — training labels and benchmark reference polygons."""
from __future__ import annotations

import time
from typing import Optional

import requests
from shapely.geometry import shape

API = "https://api.cerulean.skytruth.org/collections/public.slick_plus/items"
# Human-in-the-loop classes that confirm the slick is oil (anthropogenic or natural).
HITL_OIL = {2, 3, 4, 5, 6, 7, 8}

_session = requests.Session()


def _get(params: dict, timeout: int = 180) -> dict:
    delay = 3.0
    for _ in range(5):
        try:
            resp = _session.get(API, params=params, timeout=timeout)
            if resp.status_code == 200:
                return resp.json()
        except requests.RequestException:
            pass
        time.sleep(delay)
        delay *= 2
    raise RuntimeError("Cerulean API unavailable")


def slicks(bbox: Optional[list[float]] = None, start: Optional[str] = None, end: Optional[str] = None,
           cql: Optional[str] = None, limit: int = 100, offset: int = 0,
           properties: Optional[str] = None) -> list[dict]:
    params = {"limit": limit, "offset": offset}
    if bbox:
        params["bbox"] = ",".join(f"{v:.6f}" for v in bbox)
    if start and end:
        params["datetime"] = f"{start}/{end}"
    if cql:
        params["filter"] = cql
    if properties:
        params["properties"] = properties
    out = []
    for f in _get(params).get("features", []):
        if not f.get("geometry"):
            continue
        p = f["properties"]
        p["geometry"] = shape(f["geometry"])
        out.append(p)
    return out


def scene_slicks(scene_id: str, bbox: list[float]) -> list[dict]:
    """All published slicks in one S1 scene intersecting bbox (labels for a training chip)."""
    return slicks(bbox=bbox, cql=f"s1_scene_id = '{scene_id}'", limit=500,
                  properties="id,s1_scene_id,slick_timestamp,machine_confidence,cls,hitl_cls")
