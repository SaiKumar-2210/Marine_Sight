"""Copernicus Data Space (Sentinel Hub) Process API client."""
from __future__ import annotations

import os
import time
from datetime import datetime, timedelta
from typing import Optional

import numpy as np
import requests
from PIL import Image
from io import BytesIO

TOKEN_URL = os.environ.get(
    "CDSE_TOKEN_URL",
    "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token",
)
PROCESS_URL = os.environ.get("CDSE_PROCESS_URL", "https://sh.dataspace.copernicus.eu/api/v1/process")

S1_EVALSCRIPT = """//VERSION=3
function setup() {
  return {
    input: ["VV", "VH", "dataMask"],
    output: { bands: 3 }
  };
}
function evaluatePixel(s) {
  var vvDb = 10 * Math.log10(s.VV + 1e-6);
  var vhDb = 10 * Math.log10(s.VH + 1e-6);
  var vv = Math.max(0, Math.min(1, (vvDb + 25) / 20));
  var vh = Math.max(0, Math.min(1, (vhDb + 30) / 25));
  if (s.dataMask < 0.5) return [0, 0, 0];
  return [vv, vh, vv];
}
"""

S2_EVALSCRIPT = """//VERSION=3
function setup() {
  return {
    input: ["B02", "B03", "B04", "B08", "SCL", "dataMask"],
    output: { bands: 4 }
  };
}
function evaluatePixel(s) {
  if (s.dataMask < 0.5) return [0, 0, 0, 0];
  return [2.5 * s.B04, 2.5 * s.B03, 2.5 * s.B08, s.SCL / 11.0];
}
"""

_token_cache = {"value": None, "expires_at": 0.0}


def get_token() -> str:
    now = time.time()
    if _token_cache["value"] and now < _token_cache["expires_at"] - 60:
        return _token_cache["value"]
    client_id = os.environ.get("CDSE_CLIENT_ID", "")
    client_secret = os.environ.get("CDSE_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        raise RuntimeError("CDSE_CLIENT_ID / CDSE_CLIENT_SECRET are not set")
    resp = requests.post(
        TOKEN_URL,
        data={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
        },
        timeout=20,
    )
    resp.raise_for_status()
    payload = resp.json()
    _token_cache["value"] = payload["access_token"]
    _token_cache["expires_at"] = now + float(payload.get("expires_in", 600))
    return _token_cache["value"]


def _time_window(date_str: str, lookback_days: int, lookahead_days: int = 0) -> dict:
    day = datetime.strptime(date_str, "%Y-%m-%d")
    start = day - timedelta(days=lookback_days)
    end = day + timedelta(days=lookahead_days)
    return {
        "from": start.strftime("%Y-%m-%dT00:00:00Z"),
        "to": end.strftime("%Y-%m-%dT23:59:59Z"),
    }


def process_png(
    collection: str,
    bbox: list[float],
    date_str: str,
    width: int = 512,
    height: int = 512,
    evalscript: Optional[str] = None,
    lookback_days: int = 3,
    mosaicking: str = "mostRecent",
    max_cloud: Optional[int] = None,
) -> Optional[np.ndarray]:
    data_filter = {"timeRange": _time_window(date_str, lookback_days, 0), "mosaickingOrder": mosaicking}
    if max_cloud is not None:
        data_filter["maxCloudCoverage"] = max_cloud
    body = {
        "input": {
            "bounds": {
                "bbox": list(bbox),
                "properties": {"crs": "http://www.opengis.net/def/crs/EPSG/0/4326"},
            },
            "data": [{"type": collection, "dataFilter": data_filter}],
        },
        "output": {
            "width": width,
            "height": height,
            "responses": [{"identifier": "default", "format": {"type": "image/png"}}],
        },
        "evalscript": evalscript or (S1_EVALSCRIPT if collection.startswith("sentinel-1") else S2_EVALSCRIPT),
    }
    token = get_token()
    resp = requests.post(
        PROCESS_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "image/png",
        },
        json=body,
        timeout=45,
    )
    if resp.status_code in (400, 404):
        return None
    if resp.status_code == 429:
        time.sleep(2.0)
        return None
    resp.raise_for_status()
    img = Image.open(BytesIO(resp.content)).convert("RGBA")
    return np.array(img)


def fetch_sentinel1(bbox: list[float], date_str: str, size: int = 512) -> Optional[np.ndarray]:
    arr = process_png("sentinel-1-grd", bbox, date_str, size, size, S1_EVALSCRIPT, lookback_days=4)
    if arr is None:
        return None
    return arr[:, :, :3]


def fetch_sentinel2(bbox: list[float], date_str: str, size: int = 256) -> Optional[np.ndarray]:
    arr = process_png(
        "sentinel-2-l2a",
        bbox,
        date_str,
        size,
        size,
        S2_EVALSCRIPT,
        lookback_days=5,
        mosaicking="leastCC",
        max_cloud=95,
    )
    return arr
