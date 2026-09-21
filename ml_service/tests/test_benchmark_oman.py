"""Benchmark: the 7 Aug 2026 slick off Oman (Sentinel-1D pass 14:22 UTC, ~17.5 N 57.0 E).

Runs the production pipeline (`pipeline.scan`) for that date over the Oman AOI and compares
our extracted polygon with SkyTruth Cerulean's independent detection of the same slick
(id 5138503). This scene was held out of U-Net and verifier training.

    cd ml_service && python -m pytest -m benchmark -s
"""
import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image
from scipy import ndimage
from shapely.geometry import shape

from marinesight import cerulean
from marinesight.cdse import fetch_s1_vv_db
from marinesight.config import DATA_DIR
from marinesight.pipeline import scan
from marinesight.raster import GeoRaster, snap_bbox

DATE = "2026-08-07"
CERULEAN_ID = 5138503
REPORT = DATA_DIR / "benchmark_oman.json"


@pytest.fixture(scope="module")
def result():
    return scan(DATE, ["OMAN_ARABIAN_SEA"], ais_db=None)


@pytest.fixture(scope="module")
def reference():
    items = cerulean.slicks(cql=f"id = {CERULEAN_ID}", limit=1,
                            properties="id,slick_timestamp,machine_confidence,s1_scene_id,source_type_1_ids")
    assert items, "Cerulean reference slick not available"
    return items[0]


@pytest.mark.benchmark
def test_oman_slick_shape_matches_reference(result, reference):
    ref = reference["geometry"]
    assert result["ok"]
    hits = [s for s in result["spills"] if shape(s["geometry"]).intersects(ref)]
    assert hits, "no detection overlaps the reference slick"
    ours = max(hits, key=lambda s: shape(s["geometry"]).intersection(ref).area)
    geom = shape(ours["geometry"])

    bbox = snap_bbox([*np.array(ref.bounds[:2]) - 0.1, *np.array(ref.bounds[2:]) + 0.1])
    h = int(round((bbox[3] - bbox[1]) / (360 / 2 ** 19)))
    w = int(round((bbox[2] - bbox[0]) / (360 / 2 ** 19)))
    grid = GeoRaster(np.zeros((h, w), dtype=np.float32), bbox)
    a = grid.rasterize([geom]).astype(bool)
    b = grid.rasterize([ref]).astype(bool)
    inter, union = (a & b).sum(), (a | b).sum()
    iou = inter / union
    precision, recall = inter / a.sum(), inter / b.sum()
    # Boundary tolerance of one pixel (~75 m): how much of each outline lies on the other.
    a1, b1 = ndimage.binary_dilation(a), ndimage.binary_dilation(b)
    tol_precision, tol_recall = (a & b1).sum() / a.sum(), (b & a1).sum() / b.sum()

    # Visual comparison on the real SAR backdrop.
    vv = fetch_s1_vv_db(bbox, ours["scene"]["acquiredAt"])
    g = np.nan_to_num(np.clip((vv.data + 28) / 22, 0, 1)) * 255
    rgb = np.stack([g, g, g], -1).astype(np.uint8)
    edge = lambda m: m & ~ndimage.binary_erosion(m)  # noqa: E731
    rgb[edge(b)] = [60, 220, 255]  # Cerulean reference = cyan
    rgb[edge(a)] = [255, 60, 30]  # MarineSight = red
    Image.fromarray(rgb).save(DATA_DIR / "benchmark_oman_overlay.png")

    report = {
        "date": DATE, "scene": ours["scene"], "ourSpillId": ours["id"], "status": ours["status"],
        "oilProbability": ours["oilProbability"], "areaKm2": ours["areaKm2"], "lengthKm": ours["lengthKm"],
        "referenceAreaKm2": round(float(b.sum() * grid.pixel_area_km2), 2),
        "iou": round(float(iou), 4), "precision": round(float(precision), 4), "recall": round(float(recall), 4),
        "precision1px": round(float(tol_precision), 4), "recall1px": round(float(tol_recall), 4),
        "culprit": ours.get("culprit"), "head": (ours.get("attribution") or {}).get("head"),
        "referenceSourceMmsi": reference.get("source_type_1_ids"),
        "verification": ours["verification"]["indicators"], "metocean": ours["verification"]["metocean"],
        "stats": result["stats"],
    }
    REPORT.write_text(json.dumps(report, indent=2, default=str))
    print("\n[benchmark] " + json.dumps({k: report[k] for k in (
        "status", "oilProbability", "areaKm2", "referenceAreaKm2", "iou", "precision", "recall", "precision1px", "recall1px")}))

    assert recall >= 0.6, f"recall {recall:.2f}"
    assert iou >= 0.45, f"IoU {iou:.2f}"
    assert tol_recall >= 0.8 and tol_precision >= 0.7


@pytest.mark.benchmark
def test_oman_slick_verified_and_attributed(result, reference):
    ref = reference["geometry"]
    ours = max((s for s in result["spills"] if shape(s["geometry"]).intersects(ref)),
               key=lambda s: shape(s["geometry"]).intersection(ref).area)
    assert ours["status"] in ("confirmed", "review")
    met = ours["verification"]["metocean"]
    assert met["windMs"] is not None and met["currentMs"] is not None
    att = ours["attribution"]
    assert att is not None and att["candidates"], "attribution produced no candidates"
    # The source end of this slick is the narrow SW end off the Kuria Muria islands.
    assert att["head"]["lon"] < 56.8
