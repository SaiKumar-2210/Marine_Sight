"""Raster-to-vector: U-Net probability map -> exact slick polygons (OpenCV findContours) + geometry metrics."""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import cv2
import numpy as np
from scipy import ndimage
from shapely.geometry import MultiPolygon, Polygon, mapping
from shapely.ops import unary_union
from shapely.validation import make_valid

from .raster import GeoRaster

UPSAMPLE = 4  # trace contours on a 4x nearest-neighbour grid so 1-px-wide slicks keep their area


@dataclass
class Slick:
    geometry: MultiPolygon
    pixel_mask: np.ndarray  # bool mask for the slick's bounding window
    window: tuple[int, int, int, int]  # row0, col0, row1, col1 in the scene raster
    metrics: dict = field(default_factory=dict)

    def geojson(self) -> dict:
        return mapping(self.geometry)


def _contours_to_polygons(mask: np.ndarray, raster: GeoRaster, row0: int, col0: int) -> list[Polygon]:
    up = cv2.resize(mask.astype(np.uint8), None, fx=UPSAMPLE, fy=UPSAMPLE, interpolation=cv2.INTER_NEAREST)
    up = np.pad(up, 1)
    contours, hierarchy = cv2.findContours(up, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
    if hierarchy is None:
        return []
    hierarchy = hierarchy[0]

    def to_geo(cnt: np.ndarray) -> list[tuple[float, float]]:
        cnt = cnt.reshape(-1, 2).astype(np.float64)
        cols = col0 + (cnt[:, 0] - 1 + 0.5) / UPSAMPLE
        rows = row0 + (cnt[:, 1] - 1 + 0.5) / UPSAMPLE
        lon, lat = raster.pixel_to_lonlat(cols, rows)
        return list(zip(lon.tolist(), lat.tolist()))

    def ring_area(cnt: np.ndarray):
        # Traced rings of real slicks self-touch at pinch points; repair each ring on its own.
        g = Polygon(to_geo(cnt))
        return g if g.is_valid else make_valid(g)

    # Nesting depth from the full contour tree: even depth = slick boundary, odd = hole boundary.
    depth = [0] * len(contours)
    for i in range(len(contours)):
        p = hierarchy[i][3]
        while p != -1:
            depth[i] += 1
            p = hierarchy[p][3]
    # Apply rings level by level (add slick, cut hole, add slick-inside-hole, ...). Set algebra keeps
    # the result exact where holes touch their self-touching outer ring — repairing a shell together
    # with its holes in one polygon can flip regions inside/outside.
    geom = None
    for d in range(max(depth) + 1 if depth else 0):
        level = [ring_area(c) for i, c in enumerate(contours) if depth[i] == d and len(c) >= 3]
        if not level:
            continue
        u = unary_union(level)
        if d % 2 == 0:
            geom = u if geom is None else geom.union(u)
        elif geom is not None:
            geom = geom.difference(u)
    if geom is None or geom.is_empty:
        return []
    parts = list(geom.geoms) if hasattr(geom, "geoms") else [geom]
    return [p for p in parts if isinstance(p, Polygon) and p.area > 0]


def _metric_xy(lon: np.ndarray, lat: np.ndarray, lat0: float) -> np.ndarray:
    return np.column_stack([lon * 111.32 * math.cos(math.radians(lat0)), lat * 111.32])


def _geometry_metrics(geom: MultiPolygon, area_km2: float) -> dict:
    lat0 = geom.centroid.y
    kx = 111.32 * math.cos(math.radians(lat0))
    perim_km = 0.0
    for p in geom.geoms:
        for ring in [p.exterior, *p.interiors]:
            xy = np.asarray(ring.coords)
            d = np.diff(xy, axis=0) * [kx, 111.32]
            perim_km += float(np.hypot(d[:, 0], d[:, 1]).sum())
    hull = geom.convex_hull
    hxy = np.asarray(hull.exterior.coords) if hull.geom_type == "Polygon" else np.asarray(hull.coords)
    hxy_km = hxy * [kx, 111.32]
    diam, ends = 0.0, (0, 0)
    if len(hxy_km) > 1:
        dm = np.linalg.norm(hxy_km[:, None, :] - hxy_km[None, :, :], axis=-1)
        i, j = np.unravel_index(np.argmax(dm), dm.shape)
        diam, ends = float(dm[i, j]), (i, j)
    hull_area = float(hull.area * kx * 111.32) if hull.geom_type == "Polygon" else 0.0
    return {
        "areaKm2": round(area_km2, 4),
        "perimeterKm": round(perim_km, 3),
        "lengthKm": round(diam, 3),
        "meanWidthKm": round(area_km2 / diam, 4) if diam > 0 else None,
        "polsbyPopper": round(4 * math.pi * area_km2 / perim_km ** 2, 5) if perim_km > 0 else None,
        "fillFactor": round(area_km2 / hull_area, 4) if hull_area > 0 else None,
        "nParts": len(geom.geoms),
        "endpoints": [list(map(float, hxy[ends[0]])), list(map(float, hxy[ends[1]]))] if diam > 0 else None,
    }


def _end_profile(db: np.ndarray, rows: np.ndarray, cols: np.ndarray, dark_rows: np.ndarray, dark_cols: np.ndarray,
                 int_rows: np.ndarray, int_cols: np.ndarray, raster: GeoRaster, end_lonlat, radius_km: float) -> dict:
    """Slick width and darkness in a disc around one end — fresh oil is narrow and dark.

    Width is measured on the dark water attached to the slick (backscatter well below the sea-clutter
    background), not on the model mask, so an under-segmented weathered end still reads as wide.
    """
    exy = _metric_xy(np.array([end_lonlat[0]]), np.array([end_lonlat[1]]), end_lonlat[1])[0]

    def near(r, c):
        lon, lat = raster.pixel_to_lonlat(c + 0.5, r + 0.5)
        return np.hypot(*(_metric_xy(lon, lat, end_lonlat[1]) - exy).T) <= radius_km

    in_mask = near(rows, cols)
    in_dark = near(dark_rows, dark_cols) if len(dark_rows) else np.zeros(0, bool)
    # Darkness from interior pixels only: 1-2 px trails are mixed oil/sea pixels and read too bright.
    in_int = near(int_rows, int_cols) if len(int_rows) else np.zeros(0, bool)
    n, nd, ni = int(in_mask.sum()), int(in_dark.sum()), int(in_int.sum())
    return {
        "pixels": n,
        "interiorPixels": ni,
        "maskWidthKm": n * raster.pixel_area_km2 / radius_km if n else 0.0,
        "widthKm": max(nd, n) * raster.pixel_area_km2 / radius_km,
        "meanDb": float(np.nanmean(db[int_rows[in_int], int_cols[in_int]])) if ni else None,
    }


def dark_water(vv_db: GeoRaster, invalid: np.ndarray, margin_db: float = 2.0) -> np.ndarray:
    """Pixels at least `margin_db` below the local sea-clutter level (estimated from non-dark water)."""
    from .unet import _normconv

    valid = (~invalid & np.isfinite(vv_db.data)).astype(np.float32)
    db = np.where(valid > 0, vv_db.data, 0.0).astype(np.float32)
    n, d = _normconv(db, valid, 40)
    bg = n / np.maximum(d, 1e-3)
    sea = ((valid > 0) & (db > bg - 1.5)).astype(np.float32)
    n2, d2 = _normconv(db, sea, 40)
    nl, dl = _normconv(db, sea, 160, down=4)
    w = np.clip(d2 / 0.3, 0, 1)
    bg = np.where(dl > 1e-3, w * n2 / np.maximum(d2, 1e-3) + (1 - w) * nl / np.maximum(dl, 1e-3), bg)
    return (valid > 0) & (db < bg - margin_db)


def binarize(prob: np.ndarray, threshold: float, low: float | None = None) -> np.ndarray:
    """Plain threshold, or hysteresis: regions above `low` are kept only if they contain a pixel
    above `threshold` (weaker weathered edges attached to a confident core survive)."""
    if low is None or low >= threshold:
        return prob >= threshold
    weak = prob >= low
    lab, n = ndimage.label(weak, structure=np.ones((3, 3)))
    if n == 0:
        return weak
    strong = ndimage.maximum(prob, lab, index=np.arange(1, n + 1)) >= threshold
    keep = np.zeros(n + 1, dtype=bool)
    keep[1:] = strong
    return keep[lab]


def extract_slicks(prob: np.ndarray, vv_db: GeoRaster, invalid: np.ndarray, threshold: float,
                   min_area_km2: float = 0.1, group_km: float = 2.0, max_slicks: int = 50,
                   low: float | None = None) -> list[Slick]:
    """Threshold the U-Net output, group fragments into slicks, vectorise each exactly."""
    mask = binarize(prob, threshold, low) & ~invalid
    # Remove speckle-sized components, fill pin-holes; no opening so 1-px-wide slicks survive.
    lab, n = ndimage.label(mask, structure=np.ones((3, 3)))
    if n == 0:
        return []
    sizes = ndimage.sum(mask, lab, index=np.arange(1, n + 1))
    keep = np.zeros(n + 1, dtype=bool)
    keep[1:] = sizes >= 4
    mask = keep[lab]
    mask = mask | (ndimage.binary_fill_holes(mask) & ~mask & (ndimage.uniform_filter(mask.astype(np.float32), 3) > 0.7))

    dy_m, dx_m = vv_db.pixel_size_m
    group_px = max(1, int(round(group_km * 1000 / ((dy_m + dx_m) / 2))))
    # Fragments within group_km of each other belong to one slick (Cerulean-style grouping).
    coarse = cv2.dilate(mask.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * group_px + 1,) * 2))
    groups, ng = ndimage.label(coarse)
    objects = ndimage.find_objects(groups)
    pix_km2 = vv_db.pixel_area_km2
    db = vv_db.data
    # Dark water connected to each slick (for model-independent width at the slick ends).
    dark_lab, _ = ndimage.label(dark_water(vv_db, invalid) | mask, structure=np.ones((3, 3)))
    px_km = (dy_m + dx_m) / 2000.0
    slicks: list[Slick] = []
    for g, sl in enumerate(objects, start=1):
        if sl is None:
            continue
        r0, r1, c0, c1 = sl[0].start, sl[0].stop, sl[1].start, sl[1].stop
        m = mask[r0:r1, c0:c1] & (groups[r0:r1, c0:c1] == g)
        npx = int(m.sum())
        area = npx * pix_km2
        if area < min_area_km2:
            continue
        polys = _contours_to_polygons(m, vv_db, r0, c0)
        if not polys:
            continue
        # findContours traces the centres of the boundary (upsampled) pixels, i.e. half an upsampled
        # pixel inside the true edge. A mitred outward buffer of exactly that amount restores the
        # pixel-edge boundary (and shrinks holes back to their true size).
        pix_deg = (vv_db.bbox[2] - vv_db.bbox[0]) / vv_db.width
        geom = unary_union(polys).buffer(0.5 * pix_deg / UPSAMPLE, join_style="mitre", mitre_limit=2.0)
        geom = geom.simplify(0.02 * pix_deg, preserve_topology=True)
        geom = MultiPolygon([geom]) if geom.geom_type == "Polygon" else MultiPolygon([p for p in geom.geoms if p.geom_type == "Polygon"])

        rows, cols = np.nonzero(m)
        rows_s, cols_s = rows + r0, cols + c0
        ring = ndimage.binary_dilation(m, iterations=6) & ~m & ~invalid[r0:r1, c0:c1]
        inside_db = float(np.nanmean(db[r0:r1, c0:c1][m]))
        ring_db = float(np.nanmean(db[r0:r1, c0:c1][ring])) if ring.any() else float("nan")
        metrics = _geometry_metrics(geom, area)
        metrics.update({
            "pixelCount": npx,
            "meanProb": round(float(prob[r0:r1, c0:c1][m].mean()), 4),
            "maxProb": round(float(prob[r0:r1, c0:c1][m].max()), 4),
            "meanDb": round(inside_db, 3),
            "ringDb": round(ring_db, 3),
            "contrastDb": round(ring_db - inside_db, 3),
            "centroid": [round(geom.centroid.x, 6), round(geom.centroid.y, 6)],
        })
        if metrics["endpoints"]:
            radius = max(1.0, min(5.0, metrics["lengthKm"] * 0.08))
            ids = np.unique(dark_lab[r0:r1, c0:c1][m])
            ids = ids[ids > 0]
            pad = int(radius / px_km) + 2
            R0, C0 = max(0, r0 - pad), max(0, c0 - pad)
            R1, C1 = min(mask.shape[0], r1 + pad), min(mask.shape[1], c1 + pad)
            dr, dc = np.nonzero(np.isin(dark_lab[R0:R1, C0:C1], ids))
            ir, ic = np.nonzero(ndimage.binary_erosion(m))
            ends = [_end_profile(db, rows_s, cols_s, dr + R0, dc + C0, ir + r0, ic + c0, vv_db, e, radius)
                    for e in metrics["endpoints"]]
            metrics["endProfiles"] = ends
        slicks.append(Slick(geom, m, (r0, c0, r1, c1), metrics))
    slicks.sort(key=lambda s: s.metrics["areaKm2"] * s.metrics["meanProb"], reverse=True)
    return slicks[:max_slicks]


def scene_dark_fraction(vv_db: np.ndarray, invalid: np.ndarray) -> float:
    """Share of valid sea darker than -22 dB: high values mean a calm, look-alike-prone scene."""
    valid = ~invalid & np.isfinite(vv_db)
    if valid.sum() < 100:
        return float("nan")
    return float((vv_db[valid] < -22.0).mean())
