"""CFAR bright-target detection: vessels visible in the same Sentinel-1 pass as the slick.

A ship at the moment of acquisition is a strong point scatterer on dark sea. These
targets need no AIS, so they also reveal 'dark' vessels that switched transponders off.
Two safeguards keep islands, rocks and platforms from being reported as ships:
  * spatial: detections are clustered; clusters wider than a large ship are static structures,
  * temporal (`static_targets`): anything bright at the same spot on earlier passes is static.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
from scipy import ndimage

from .raster import GeoRaster

KM_PER_DEG = 111.32


def _cfar(db: np.ndarray, valid: np.ndarray, k_sigma: float, window: int = 41) -> np.ndarray:
    lin = np.where(valid, 10 ** (np.nan_to_num(db, nan=-40.0) / 10), 0.0).astype(np.float32)
    w = valid.astype(np.float32)
    n = ndimage.uniform_filter(w, window)
    mu = ndimage.uniform_filter(lin, window) / np.maximum(n, 1e-3)
    mu2 = ndimage.uniform_filter(lin * lin, window) / np.maximum(n, 1e-3)
    sd = np.sqrt(np.maximum(mu2 - mu * mu, 1e-12))
    return valid & (lin > mu + k_sigma * sd)


def detect_sar_vessels(vv_db: GeoRaster, invalid: np.ndarray, k_sigma: float = 6.0, min_peak_db: float = 0.0,
                       cluster_m: float = 500.0, max_vessel_extent_m: float = 600.0) -> list[dict]:
    db = vv_db.data
    valid = ~invalid & np.isfinite(db)
    hits = _cfar(db, valid, k_sigma) & (db > min_peak_db)
    # Keep well away from land/no-data edges where clutter statistics are unreliable.
    hits &= ndimage.binary_erosion(valid, iterations=4)
    dy, dx = vv_db.pixel_size_m
    px_m = (dy + dx) / 2
    # Cluster detections within `cluster_m`: one ship can split into several bright pixels,
    # and an island's rocky shore shows up as a scatter of bright points.
    grow = max(1, int(round(cluster_m / 2 / px_m)))
    clusters, count = ndimage.label(ndimage.binary_dilation(hits, iterations=grow))
    out = []
    for i, sl in enumerate(ndimage.find_objects(clusters), start=1):
        if sl is None:
            continue
        comp = hits[sl] & (clusters[sl] == i)
        rows, cols = np.nonzero(comp)
        if len(rows) == 0:
            continue
        r = rows + sl[0].start
        c = cols + sl[1].start
        extent_m = float(max(np.ptp(r) + 1, np.ptp(c) + 1) * px_m)
        vals = db[r, c]
        k = int(np.argmax(vals))
        lon, lat = vv_db.pixel_to_lonlat(c[k] + 0.5, r[k] + 0.5)
        out.append({
            "lon": round(float(lon), 6), "lat": round(float(lat), 6),
            "peakDb": round(float(vals[k]), 2), "pixels": int(len(r)), "extentM": round(extent_m, 1),
            "static": extent_m > max_vessel_extent_m or None,  # None = not yet checked over time
            "staticReason": "extended bright cluster (island / structure)" if extent_m > max_vessel_extent_m else None,
        })
    out.sort(key=lambda v: v["peakDb"], reverse=True)
    return out[:2000]


def static_targets(targets: list[dict], acquired: datetime, max_scenes: int = 3, radius_m: float = 200.0) -> None:
    """Mark targets that are also bright on earlier passes as static (in place)."""
    from .cdse import CdseError, fetch_s1_vv_db, search

    for t in targets:
        if t.get("static") is not None:
            continue
        half = 0.012
        bbox = [t["lon"] - half, t["lat"] - half, t["lon"] + half, t["lat"] + half]
        try:
            scenes = search("sentinel-1-grd", bbox, acquired - timedelta(days=36), acquired - timedelta(hours=6))
        except CdseError:
            continue
        # Most recent distinct passes first.
        seen, prior = set(), []
        for f in reversed(scenes):
            key = f["properties"]["datetime"][:16]
            if key not in seen:
                seen.add(key)
                prior.append(f["properties"]["datetime"])
            if len(prior) >= max_scenes:
                break
        hits = 0
        for when in prior:
            try:
                chip = fetch_s1_vv_db(bbox, when)
            except CdseError:
                continue
            valid = np.isfinite(chip.data)
            if valid.mean() < 0.5:
                continue
            col, row = chip.lonlat_to_pixel(t["lon"], t["lat"])
            r_px = max(1, int(round(radius_m / ((chip.pixel_size_m[0] + chip.pixel_size_m[1]) / 2))))
            r0, c0 = int(row), int(col)
            win = chip.data[max(0, r0 - r_px):r0 + r_px + 1, max(0, c0 - r_px):c0 + r_px + 1]
            bright = _cfar(chip.data, valid, 6.0, window=21)[max(0, r0 - r_px):r0 + r_px + 1, max(0, c0 - r_px):c0 + r_px + 1]
            if np.any(bright & (win > 0.0)):
                hits += 1
        t["priorPassesChecked"] = len(prior)
        t["priorPassesBright"] = hits
        # Speckle can hide a rock on one pass; ships almost never sit on the same 200 m spot on other days.
        t["static"] = bool(prior) and hits / len(prior) >= 0.5
        if t["static"]:
            t["staticReason"] = f"bright at the same spot on {hits}/{len(prior)} earlier Sentinel-1 passes"
