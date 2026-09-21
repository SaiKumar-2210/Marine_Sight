"""Raster-to-vector exactness: the polygon must reproduce the mask pixel-for-pixel on the map."""
import math

import numpy as np
from shapely.geometry import Polygon

from marinesight.config import PIX_DEG
from marinesight.raster import GeoRaster
from marinesight.segment import extract_slicks

BBOX = [57.0, 17.0, 57.0 + 400 * PIX_DEG, 17.0 + 300 * PIX_DEG]


def _scene(mask: np.ndarray) -> tuple[np.ndarray, GeoRaster, np.ndarray]:
    db = np.full(mask.shape, -15.0, dtype=np.float32)
    db[mask] = -24.0
    prob = mask.astype(np.float32)
    return prob, GeoRaster(db, BBOX), np.zeros(mask.shape, dtype=bool)


def _area_px(geom, raster: GeoRaster) -> float:
    return geom.area / (PIX_DEG * PIX_DEG)


def test_blob_with_hole_is_exact():
    mask = np.zeros((300, 400), dtype=bool)
    mask[100:160, 50:200] = True
    mask[120:140, 90:120] = False  # hole (a clean-water pocket inside the slick)
    prob, vv, invalid = _scene(mask)
    slicks = extract_slicks(prob, vv, invalid, 0.5, min_area_km2=0.01)
    assert len(slicks) == 1
    s = slicks[0]
    assert abs(_area_px(s.geometry, vv) - mask.sum()) / mask.sum() < 0.01
    poly = max(s.geometry.geoms, key=lambda p: p.area)
    assert len(poly.interiors) == 1, "hole must be preserved"
    # West edge of the blob is the left edge of column 50.
    minx, miny, maxx, maxy = s.geometry.bounds
    assert math.isclose(minx, BBOX[0] + 50 * PIX_DEG, abs_tol=PIX_DEG * 0.2)
    assert math.isclose(maxy, BBOX[3] - 100 * PIX_DEG, abs_tol=PIX_DEG * 0.2)


def test_one_pixel_wide_slick_keeps_its_area():
    mask = np.zeros((300, 400), dtype=bool)
    rr = np.arange(20, 280)
    cc = (60 + rr * 0.9).astype(int)  # diagonal 1-px trail, typical of a fresh ship discharge
    mask[rr, cc] = True
    mask[rr, cc + 1] = True
    prob, vv, invalid = _scene(mask)
    slicks = extract_slicks(prob, vv, invalid, 0.5, min_area_km2=0.01)
    assert len(slicks) == 1
    got = _area_px(slicks[0].geometry, vv)
    assert abs(got - mask.sum()) / mask.sum() < 0.03
    assert slicks[0].metrics["lengthKm"] > 20


def test_fragments_group_into_one_slick_and_far_ones_split():
    mask = np.zeros((300, 400), dtype=bool)
    mask[50:54, 50:120] = True
    mask[50:54, 125:200] = True  # 5 px (~370 m) gap -> same slick
    mask[250:260, 300:390] = True  # far away -> separate slick
    prob, vv, invalid = _scene(mask)
    slicks = extract_slicks(prob, vv, invalid, 0.5, min_area_km2=0.01)
    assert len(slicks) == 2
    parts = sorted(s.metrics["nParts"] for s in slicks)
    assert parts == [1, 2]


def test_land_is_excluded():
    mask = np.zeros((300, 400), dtype=bool)
    mask[100:150, 100:200] = True
    prob, vv, invalid = _scene(mask)
    invalid[:, :150] = True
    s = extract_slicks(prob, vv, invalid, 0.5, min_area_km2=0.01)[0]
    assert abs(_area_px(s.geometry, vv) - 50 * 50) / 2500 < 0.01


def test_realistic_fragmented_slick_polygon_reproduces_mask():
    """Noisy, pinched, holey shapes like real weathered slicks (the case that exposed a make_valid bug)."""
    from scipy import ndimage

    rng = np.random.default_rng(3)
    field = ndimage.gaussian_filter(rng.normal(size=(300, 400)), 3)
    mask = field > 0.02  # many touching lobes, pinch points, holes and islands-in-holes
    prob, vv, invalid = _scene(mask)
    slicks = extract_slicks(prob, vv, invalid, 0.5, min_area_km2=0.0, group_km=50)
    kept = np.zeros_like(mask)
    raster = np.zeros_like(mask)
    for s in slicks:
        r0, c0, r1, c1 = s.window
        kept[r0:r1, c0:c1] |= s.pixel_mask
        raster |= vv.rasterize([s.geometry]).astype(bool)
    iou = (kept & raster).sum() / (kept | raster).sum()
    assert iou > 0.995, f"polygon/mask IoU {iou:.4f}"
