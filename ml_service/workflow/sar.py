"""Stage 1: Sentinel-1 dark-slick detection (CFAR morphology + CNN)."""
from __future__ import annotations

from typing import Optional

import cv2
import numpy as np
from scipy import ndimage

from .cnn import SarOilClassifier
from .geo import pixel_size_m, pixel_to_latlng


def _valid_mask(vv: np.ndarray) -> np.ndarray:
    return vv > 4


def _scene_is_low_wind(dark_frac: float, vv: np.ndarray, valid: np.ndarray) -> bool:
    if valid.sum() < 200:
        return True
    p20 = float(np.percentile(vv[valid], 20))
    p80 = float(np.percentile(vv[valid], 80))
    # Calm seas: most of the ocean is dark and low-contrast.
    return dark_frac > 0.38 or (p80 - p20) < 18


def _elongation(ys: np.ndarray, xs: np.ndarray) -> float:
    if len(xs) < 8:
        return 1.0
    coords = np.column_stack([xs.astype(float), ys.astype(float)])
    cov = np.cov(coords, rowvar=False)
    if cov.size == 1:
        return 1.0
    eig = np.linalg.eigvalsh(cov)
    eig = np.sort(eig)
    if eig[-1] <= 1e-6:
        return 1.0
    return float(np.sqrt(max(eig[-1], 1e-6) / max(eig[0], 1e-6)))


def _contour_polygon(mask: np.ndarray, bbox, width: int, height: int) -> list[list[float]]:
    m = (mask.astype(np.uint8) * 255)
    contours, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        ys, xs = np.where(mask)
        if len(xs) == 0:
            return []
        pts = []
        for x, y in zip(xs[:: max(1, len(xs) // 24)], ys[:: max(1, len(ys) // 24)]):
            lat, lng = pixel_to_latlng(x, y, bbox, width, height)
            pts.append([lat, lng])
        return pts
    contour = max(contours, key=cv2.contourArea)
    peri = cv2.arcLength(contour, True)
    approx = cv2.approxPolyDP(contour, 0.012 * peri, True)
    if len(approx) < 6:
        approx = cv2.approxPolyDP(contour, 0.006 * peri, True)
    pts = []
    for p in approx.reshape(-1, 2):
        lat, lng = pixel_to_latlng(float(p[0]), float(p[1]), bbox, width, height)
        pts.append([lat, lng])
    if pts and pts[0] != pts[-1]:
        pts.append(pts[0])
    return pts


def detect_dark_slicks(
    rgb: np.ndarray,
    bbox: list[float],
    cnn: Optional[SarOilClassifier] = None,
    max_candidates: int = 4,
) -> dict:
    """Return SAR candidates from a VV/VH visualization tile."""
    height, width = rgb.shape[:2]
    vv = rgb[:, :, 0].astype(np.float32)
    valid = _valid_mask(vv)
    valid_frac = float(valid.mean())
    if valid_frac < 0.25:
        return {"candidates": [], "skipped": "nodata", "darkFraction": 0.0, "validFraction": valid_frac}

    sea = ndimage.uniform_filter(vv, size=31)
    local_std = np.sqrt(np.maximum(ndimage.uniform_filter(vv ** 2, size=31) - sea ** 2, 0))
    sea_med = float(np.median(vv[valid]))
    # Adaptive CFAR: darker than local sea clutter.
    dark = (vv < (sea - 0.65 * np.maximum(local_std, 8.0))) & (vv < sea_med * 0.78) & valid
    dark = ndimage.binary_opening(dark, iterations=1)
    dark = ndimage.binary_closing(dark, iterations=1)
    dark_frac = float(dark[valid].mean()) if valid.any() else 0.0

    if _scene_is_low_wind(dark_frac, vv, valid):
        return {
            "candidates": [],
            "skipped": "low_wind_lookalike",
            "darkFraction": round(dark_frac, 3),
            "validFraction": round(valid_frac, 3),
        }

    labeled, nlab = ndimage.label(dark)
    py_m, px_m = pixel_size_m(bbox, width, height)
    pix_area_km2 = (py_m * px_m) / 1e6
    candidates = []

    for lab in range(1, nlab + 1):
        comp = labeled == lab
        area_px = int(comp.sum())
        area_km2 = area_px * pix_area_km2
        if area_px < 28 or area_km2 < 0.04:
            continue
        if area_km2 > 80:
            continue
        if area_px > 0.18 * valid.sum():
            continue

        ys, xs = np.where(comp)
        elong = _elongation(ys, xs)
        region_mean = float(vv[comp].mean())
        ring = ndimage.binary_dilation(comp, iterations=10) & ~comp & valid
        if ring.sum() < 20:
            continue
        surround = float(vv[ring].mean())
        if surround <= 1e-3:
            continue
        contrast = (surround - region_mean) / surround
        if contrast < 0.12:
            continue

        # Prefer elongated ship-discharge trails; keep compact platform leaks if dark enough.
        if elong < 1.6 and contrast < 0.22:
            continue

        y0, y1 = max(0, ys.min() - 8), min(height, ys.max() + 9)
        x0, x1 = max(0, xs.min() - 8), min(width, xs.max() + 9)
        chip = rgb[y0:y1, x0:x1]
        cnn_prob = cnn.predict_array(chip) if cnn is not None else None

        physical = float(np.clip(0.45 * min(contrast / 0.45, 1.0) + 0.35 * min(elong / 6.0, 1.0) + 0.20 * min(area_km2 / 8.0, 1.0), 0, 1))
        if cnn_prob is None:
            sar_score = physical
        else:
            # Domain gap: Copernicus stretch ≠ Kaggle JPEG. Keep physics in the lead.
            sar_score = 0.58 * physical + 0.42 * float(cnn_prob)

        if sar_score < 0.42:
            continue

        cy, cx = float(ys.mean()), float(xs.mean())
        lat, lng = pixel_to_latlng(cx, cy, bbox, width, height)
        polygon = _contour_polygon(comp, bbox, width, height)
        if len(polygon) < 4:
            continue

        candidates.append({
            "lat": round(lat, 5),
            "lng": round(lng, 5),
            "areaKm2": round(float(area_km2), 2),
            "elongation": round(elong, 2),
            "contrast": round(contrast, 3),
            "cnnProbability": None if cnn_prob is None else round(float(cnn_prob), 4),
            "physicalScore": round(physical * 100.0, 1),
            "sarScore": round(sar_score * 100.0, 1),
            "polygon": polygon,
            "chipBbox": [
                pixel_to_latlng(x0, y1, bbox, width, height)[1],
                pixel_to_latlng(x0, y1, bbox, width, height)[0],
                pixel_to_latlng(x1, y0, bbox, width, height)[1],
                pixel_to_latlng(x1, y0, bbox, width, height)[0],
            ],
            "chip": chip,
        })

    candidates.sort(key=lambda c: c["sarScore"], reverse=True)
    return {
        "candidates": candidates[:max_candidates],
        "skipped": None,
        "darkFraction": round(dark_frac, 3),
        "validFraction": round(valid_frac, 3),
        "components": int(nlab),
    }
