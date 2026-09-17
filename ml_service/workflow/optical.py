"""Stage 2: Sentinel-2 optical confirmation of a SAR candidate."""
from __future__ import annotations

from typing import Optional

import numpy as np

from .copernicus import fetch_sentinel2
from .geo import pad_bbox


SCL_CLOUD = {3, 8, 9, 10}  # cloud shadow, cloud medium/high, cirrus
SCL_WATER = {6}


def confirm_optical(candidate: dict, date_str: str) -> dict:
    chip_bbox = candidate.get("chipBbox")
    if not chip_bbox:
        lat, lng = candidate["lat"], candidate["lng"]
        chip_bbox = [lng - 0.08, lat - 0.08, lng + 0.08, lat + 0.08]
    bbox = pad_bbox(chip_bbox, 0.02)

    arr = fetch_sentinel2(bbox, date_str, size=256)
    if arr is None:
        return {
            "status": "no_scene",
            "score": None,
            "cloudFraction": None,
            "note": "No Sentinel-2 scene in ±5 day window",
            "image": None,
        }

    rgb_nir = arr[:, :, :3].astype(np.float32) / 255.0
    scl = np.round(arr[:, :, 3].astype(np.float32) * 11.0).astype(int)
    cloud = np.isin(scl, list(SCL_CLOUD))
    nodata = arr[:, :, 3] < 0.02
    usable = ~nodata
    cloud_frac = float(cloud[usable].mean()) if usable.any() else 1.0

    if cloud_frac > 0.55:
        return {
            "status": "cloudy",
            "score": None,
            "cloudFraction": round(cloud_frac * 100.0, 1),
            "note": f"Scene too cloudy ({cloud_frac*100:.0f}%) for optical confirmation",
            "image": arr[:, :, :3],
        }

    vis = rgb_nir[:, :, :2].mean(axis=2)  # R,G ~ red/green
    nir = rgb_nir[:, :, 2]
    h, w = vis.shape
    cy, cx = h // 2, w // 2
    yy, xx = np.ogrid[:h, :w]
    inner = (yy - cy) ** 2 + (xx - cx) ** 2 <= (min(h, w) * 0.22) ** 2
    ring = ((yy - cy) ** 2 + (xx - cx) ** 2 <= (min(h, w) * 0.42) ** 2) & ~inner
    inner &= usable & ~cloud
    ring &= usable & ~cloud
    if inner.sum() < 30 or ring.sum() < 40:
        return {
            "status": "inconclusive",
            "score": None,
            "cloudFraction": round(cloud_frac * 100.0, 1),
            "note": "Not enough clear water pixels around the SAR footprint",
            "image": arr[:, :, :3],
        }

    inner_vis = float(vis[inner].mean())
    ring_vis = float(vis[ring].mean())
    inner_nir = float(nir[inner].mean())
    ring_nir = float(nir[ring].mean())
    vis_contrast = (ring_vis - inner_vis) / max(ring_vis, 1e-3)
    nir_contrast = (ring_nir - inner_nir) / max(ring_nir, 1e-3)

    # Algae / vegetation: NIR brighter than surroundings.
    if nir_contrast < -0.12 and inner_nir > inner_vis:
        return {
            "status": "rejected",
            "score": 18.0,
            "cloudFraction": round(cloud_frac * 100.0, 1),
            "note": "NIR bright relative to water — likely biogenic / algal look-alike",
            "image": arr[:, :, :3],
            "visContrast": round(vis_contrast, 3),
            "nirContrast": round(nir_contrast, 3),
        }

    if vis_contrast >= 0.10 and nir_contrast >= 0.05:
        status = "confirmed"
        score = float(np.clip(55 + 90 * vis_contrast + 40 * nir_contrast, 55, 95))
        note = "Optical darkening vs surrounding water (visible + NIR)"
    elif vis_contrast >= 0.05:
        status = "weak"
        score = float(np.clip(40 + 80 * vis_contrast, 40, 70))
        note = "Weak optical darkening; possible thin sheen"
    else:
        status = "rejected"
        score = float(np.clip(25 + 40 * max(vis_contrast, 0), 10, 45))
        note = "No optical darkening at SAR location"

    return {
        "status": status,
        "score": round(score, 1),
        "cloudFraction": round(cloud_frac * 100.0, 1),
        "note": note,
        "image": arr[:, :, :3],
        "visContrast": round(vis_contrast, 3),
        "nirContrast": round(nir_contrast, 3),
    }
