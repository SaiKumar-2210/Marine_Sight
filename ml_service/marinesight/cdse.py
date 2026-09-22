"""Copernicus Data Space Ecosystem (Sentinel Hub) client: auth, catalog search, raster fetch."""
from __future__ import annotations

import hashlib
import io
import json
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import numpy as np
import rasterio
import requests

from .config import CACHE_DIR, PIX_DEG
from .raster import GeoRaster, bbox_shape, mosaic, snap_bbox, split_tiles

TOKEN_URL = os.environ.get(
    "CDSE_TOKEN_URL", "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
)
PROCESS_URL = os.environ.get("CDSE_PROCESS_URL", "https://sh.dataspace.copernicus.eu/api/v1/process")
CATALOG_URL = "https://sh.dataspace.copernicus.eu/api/v1/catalog/1.0.0/search"

RASTER_CACHE = CACHE_DIR / "rasters"

# VV sigma0 in dB, packed into UINT16 as (dB + 50) * 1000; 0 = no data.
S1_VV_DB_EVALSCRIPT = """//VERSION=3
function setup(){return {input:["VV","dataMask"],output:{bands:1,sampleType:"UINT16"}};}
function evaluatePixel(s){
  if (s.dataMask < 1 || s.VV <= 0) return [0];
  var db = 10 * Math.log(s.VV) / Math.LN10;
  return [Math.max(1, Math.min(65535, (db + 50) * 1000))];
}"""

S2_BANDS = ["B02", "B03", "B04", "B08", "B11"]
# Surface reflectance * 10000 for B02,B03,B04,B08,B11 then the scene classification layer; 0 SCL = no data.
S2_EVALSCRIPT = """//VERSION=3
function setup(){return {input:["B02","B03","B04","B08","B11","SCL","dataMask"],output:{bands:6,sampleType:"UINT16"}};}
function evaluatePixel(s){
  if (s.dataMask < 1) return [0,0,0,0,0,0];
  return [s.B02*10000, s.B03*10000, s.B04*10000, s.B08*10000, s.B11*10000, s.SCL];
}"""


class CdseError(RuntimeError):
    pass


_token_lock = threading.Lock()
_token = {"value": None, "expires_at": 0.0}
_session = requests.Session()


def get_token() -> str:
    with _token_lock:
        now = time.time()
        if _token["value"] and now < _token["expires_at"] - 60:
            return _token["value"]
        cid, secret = os.environ.get("CDSE_CLIENT_ID"), os.environ.get("CDSE_CLIENT_SECRET")
        if not cid or not secret:
            raise CdseError("CDSE_CLIENT_ID / CDSE_CLIENT_SECRET are not configured")
        resp = _session.post(
            TOKEN_URL,
            data={"grant_type": "client_credentials", "client_id": cid, "client_secret": secret},
            timeout=30,
        )
        if resp.status_code != 200:
            raise CdseError(f"CDSE authentication failed ({resp.status_code})")
        payload = resp.json()
        _token["value"] = payload["access_token"]
        _token["expires_at"] = now + float(payload.get("expires_in", 600))
        return _token["value"]


def _post(url: str, body: dict, accept: str, timeout: int = 180) -> requests.Response:
    delay = 2.0
    for attempt in range(6):
        resp = _session.post(
            url,
            json=body,
            headers={"Authorization": f"Bearer {get_token()}", "Accept": accept},
            timeout=timeout,
        )
        if resp.status_code == 429 or resp.status_code >= 500:
            time.sleep(delay)
            delay = min(delay * 2, 30)
            continue
        if resp.status_code == 401 and attempt == 0:
            _token["value"] = None
            continue
        if resp.status_code == 403:
            raise CdseError("CDSE refused the request (403) — processing-unit quota may be exhausted")
        return resp
    raise CdseError(f"CDSE request kept failing ({resp.status_code})")


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def day_window(date_str: str) -> tuple[datetime, datetime]:
    start = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return start, start + timedelta(days=1) - timedelta(seconds=1)


def search(collection: str, bbox: list[float], start: datetime, end: datetime, limit: int = 100) -> list[dict]:
    """STAC catalog search; returns features sorted by acquisition time.

    Note: the CDSE catalog rejects `sortby`, so callers that want the most recent scene must ask
    for a bounded window and take the last feature.
    """
    body = {
        "bbox": list(bbox),
        "datetime": f"{_iso(start)}/{_iso(end)}",
        "collections": [collection],
        "limit": limit,
    }
    resp = _post(CATALOG_URL, body, "application/geo+json", timeout=60)
    if resp.status_code != 200:
        raise CdseError(f"Catalog search failed ({resp.status_code}): {resp.text[:200]}")
    feats = resp.json().get("features", [])
    return sorted(feats, key=lambda f: f["properties"]["datetime"])


def s1_scenes(bbox: list[float], date_str: str) -> list[dict]:
    """IW GRD scenes over bbox on a UTC date, as {id, datetime, footprint(shapely)}."""
    from shapely.geometry import shape

    start, end = day_window(date_str)
    out = []
    for f in search("sentinel-1-grd", bbox, start, end):
        p = f["properties"]
        if p.get("sar:instrument_mode") not in (None, "IW"):
            continue
        pols = p.get("s1:polarization") or ""
        if pols and "V" not in pols:  # need VV
            continue
        out.append({
            "id": f["id"],
            "datetime": p["datetime"],
            "footprint": shape(f["geometry"]),
            "orbitState": p.get("sat:orbit_state"),
            "platform": f["id"][:3],
        })
    return out


def _cache_path(kind: str, key: dict):
    digest = hashlib.sha1(json.dumps(key, sort_keys=True).encode()).hexdigest()[:20]
    return RASTER_CACHE / f"{kind}_{digest}.npy"


def _process_tiff(collection: str, bbox: list[float], width: int, height: int, evalscript: str,
                  data_filter: dict, processing: Optional[dict] = None) -> np.ndarray:
    data = {"type": collection, "dataFilter": data_filter}
    if processing:
        data["processing"] = processing
    body = {
        "input": {
            "bounds": {"bbox": bbox, "properties": {"crs": "http://www.opengis.net/def/crs/EPSG/0/4326"}},
            "data": [data],
        },
        "output": {"width": width, "height": height,
                   "responses": [{"identifier": "default", "format": {"type": "image/tiff"}}]},
        "evalscript": evalscript,
    }
    resp = _post(PROCESS_URL, body, "image/tiff")
    if resp.status_code != 200:
        raise CdseError(f"Process API {collection} failed ({resp.status_code}): {resp.text[:300]}")
    with rasterio.open(io.BytesIO(resp.content)) as ds:
        return ds.read()


def fetch_s1_vv_db(bbox: list[float], acquired: str, pix: float = PIX_DEG) -> GeoRaster:
    """Sentinel-1 VV backscatter (dB, float32, NaN = no data) for one acquisition.

    `acquired` is the scene's ISO datetime; a ±2 minute window isolates that pass.
    """
    bbox = snap_bbox(bbox, pix)
    t = datetime.fromisoformat(acquired.replace("Z", "+00:00"))
    data_filter = {
        "timeRange": {"from": _iso(t - timedelta(minutes=2)), "to": _iso(t + timedelta(minutes=2))},
        "acquisitionMode": "IW",
        "resolution": "HIGH",
        "mosaickingOrder": "mostRecent",
    }
    processing = {"backCoeff": "SIGMA0_ELLIPSOID", "orthorectify": True, "demInstance": "COPERNICUS_30"}
    tiles = []
    for tb in split_tiles(bbox, pix=pix):
        h, w = bbox_shape(tb, pix)
        key = {"c": "s1vv", "bbox": [round(v, 7) for v in tb], "t": acquired, "px": pix}
        path = _cache_path("s1", key)
        if path.exists():
            arr = np.load(path)
        else:
            arr = _process_tiff("sentinel-1-grd", tb, w, h, S1_VV_DB_EVALSCRIPT, data_filter, processing)[0]
            path.parent.mkdir(parents=True, exist_ok=True)
            np.save(path, arr)
        tiles.append(GeoRaster(arr, tb))
    packed = mosaic(tiles, bbox).data
    db = np.where(packed > 0, packed.astype(np.float32) / 1000.0 - 50.0, np.nan).astype(np.float32)
    return GeoRaster(db, bbox)


def best_s2_scene(bbox: list[float], around: datetime, max_hours: float = 36.0) -> Optional[dict]:
    """Closest-in-time Sentinel-2 L2A granule with acceptable cloud cover."""
    feats = search("sentinel-2-l2a", bbox, around - timedelta(hours=max_hours), around + timedelta(hours=max_hours))
    best = None
    for f in feats:
        p = f["properties"]
        cc = p.get("eo:cloud_cover")
        if cc is not None and cc > 80:
            continue
        t = datetime.fromisoformat(p["datetime"].replace("Z", "+00:00"))
        dt_h = (t - around).total_seconds() / 3600.0
        score = abs(dt_h) + (cc or 0) * 0.1
        if best is None or score < best["score"]:
            best = {"id": f["id"], "datetime": p["datetime"], "cloudCover": cc, "dtHours": round(dt_h, 2), "score": score}
    return best


def fetch_s2(bbox: list[float], acquired: str, max_px: int = 512) -> GeoRaster:
    """Sentinel-2 L2A reflectance bands (float32, 0-1) + SCL for one acquisition, shape (6, H, W)."""
    w, s, e, n = bbox
    pix = max((e - w), (n - s)) / max_px
    pix = max(pix, 0.0001)  # never finer than ~10 m
    bbox = snap_bbox(bbox, pix)
    h, wpx = bbox_shape(bbox, pix)
    t = datetime.fromisoformat(acquired.replace("Z", "+00:00"))
    key = {"c": "s2", "bbox": [round(v, 7) for v in bbox], "t": acquired, "px": pix}
    path = _cache_path("s2", key)
    if path.exists():
        arr = np.load(path)
    else:
        data_filter = {
            "timeRange": {"from": _iso(t - timedelta(minutes=30)), "to": _iso(t + timedelta(minutes=30))},
            "mosaickingOrder": "leastCC",
        }
        arr = _process_tiff("sentinel-2-l2a", bbox, wpx, h, S2_EVALSCRIPT, data_filter)
        path.parent.mkdir(parents=True, exist_ok=True)
        np.save(path, arr)
    out = arr.astype(np.float32)
    out[:5] /= 10000.0
    return GeoRaster(out, bbox)
