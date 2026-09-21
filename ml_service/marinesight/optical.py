"""Sentinel-2 optical evidence for a SAR slick candidate (only run when the SAR model flags one)."""
from __future__ import annotations

import math
from datetime import datetime

import numpy as np
from scipy import ndimage
from shapely import affinity

from .cdse import best_s2_scene, fetch_s2
from .metocean import drift_velocity

SCL_CLOUD = (3, 8, 9, 10)  # cloud shadow, cloud medium/high probability, thin cirrus
SCL_WATER = 6
# Floating Algae Index baseline factor (Hu 2009): (λNIR - λRed) / (λSWIR - λRed) = (842-665)/(1610-665)
FAI_K = (842 - 665) / (1610 - 665)

EMPTY = {
    "s2Available": 0, "s2DtHours": np.nan, "s2CloudFrac": np.nan, "s2VisContrast": np.nan,
    "s2NirContrast": np.nan, "s2SwirContrast": np.nan, "s2FaiDiff": np.nan, "s2NdviDiff": np.nan,
    "s2Glint": np.nan, "s2InsidePx": 0,
}


def _rel(a: float, b: float) -> float:
    return (a - b) / max(abs(b), 1e-4)


def s2_bbox(geom) -> list[float]:
    minx, miny, maxx, maxy = geom.bounds
    pad = max(0.05, 0.25 * max(maxx - minx, maxy - miny))
    return [minx - pad, miny - pad, maxx + pad, maxy + pad]


def load_s2(bbox: list[float], acquired: datetime, max_px: int = 512):
    """(scene, raster) for the closest usable Sentinel-2 pass, or (None, None)."""
    scene = best_s2_scene(bbox, acquired)
    if scene is None:
        return None, None
    return scene, fetch_s2(bbox, scene["datetime"], max_px=max_px)


def optical_features(geom, acquired: datetime, met: dict, want_image: bool = False,
                     preloaded=None) -> tuple[dict, dict]:
    """Returns (features, info). Features are NaN when no usable S2 pass exists within ±36 h."""
    scene, s2 = preloaded if preloaded is not None else load_s2(s2_bbox(geom), acquired)
    if scene is None:
        return dict(EMPTY), {"status": "no_scene", "note": "No Sentinel-2 pass within ±36 h"}
    bands, scl = s2.data[:5], s2.data[5].astype(np.int32)
    b02, b03, b04, b08, b11 = bands
    nodata = scl == 0
    cloud = np.isin(scl, SCL_CLOUD)

    # Shift the slick to where the drift model puts it at the S2 overpass time.
    dt_s = scene["dtHours"] * 3600.0
    u, v = drift_velocity(met)
    lat0 = geom.centroid.y
    dlon = u * dt_s / (111_320.0 * math.cos(math.radians(lat0)))
    dlat = v * dt_s / 111_320.0
    moved = affinity.translate(geom, xoff=dlon, yoff=dlat)
    inside = s2.rasterize([moved], all_touched=True).astype(bool)
    inside = ndimage.binary_dilation(inside, iterations=1)
    ring = ndimage.binary_dilation(inside, iterations=8) & ~inside
    usable = ~nodata & ~cloud
    area = ndimage.binary_dilation(inside, iterations=8)
    cloud_frac = float(cloud[area & ~nodata].mean()) if (area & ~nodata).any() else 1.0
    info = {"scene": scene["id"], "datetime": scene["datetime"], "dtHours": scene["dtHours"],
            "tileCloudCover": scene["cloudCover"], "cloudFrac": round(cloud_frac, 3),
            "driftShiftKm": round(math.hypot(u, v) * abs(dt_s) / 1000.0, 2)}
    if want_image:
        rgb = np.stack([b04, b03, b02], -1)
        info["rgb"] = (np.clip(rgb / 0.12, 0, 1) ** 0.8 * 255).astype(np.uint8)
        info["bbox"] = s2.bbox
    ins, rng = inside & usable, ring & usable
    if ins.sum() < 8 or rng.sum() < 20:
        feats = dict(EMPTY)
        feats.update({"s2Available": 1, "s2DtHours": abs(scene["dtHours"]), "s2CloudFrac": cloud_frac})
        info.update(status="cloudy" if cloud_frac > 0.5 else "inconclusive",
                    note="Not enough clear-water S2 pixels over the drift-corrected slick")
        return feats, info

    def m(a, sel):
        return float(np.mean(a[sel]))

    vis = (b02 + b03 + b04) / 3.0
    fai = b08 - (b04 + (b11 - b04) * FAI_K)
    ndvi = (b08 - b04) / np.maximum(b08 + b04, 1e-4)
    feats = {
        "s2Available": 1,
        "s2DtHours": abs(scene["dtHours"]),
        "s2CloudFrac": cloud_frac,
        "s2VisContrast": _rel(m(vis, ins), m(vis, rng)),
        "s2NirContrast": _rel(m(b08, ins), m(b08, rng)),
        "s2SwirContrast": _rel(m(b11, ins), m(b11, rng)),
        "s2FaiDiff": m(fai, ins) - m(fai, rng),
        "s2NdviDiff": m(ndvi, ins) - m(ndvi, rng),
        "s2Glint": m(b08, rng),
        "s2InsidePx": int(ins.sum()),
    }
    algae = feats["s2FaiDiff"] > 0.01 and feats["s2NdviDiff"] > 0.05
    info.update(status="algae_signature" if algae else "clear",
                note=("Floating-algae spectral signature (FAI/NDVI up) over the slick" if algae else
                      f"Clear S2 view, visible contrast {feats['s2VisContrast']:+.2f}, FAI Δ {feats['s2FaiDiff']:+.4f}"))
    return feats, info
