"""Mine hard negatives for the verifier: real Sentinel-1 passes in the two classic look-alike regimes.

  lowwind — calm sea (ERA5 10 m wind < 3.5 m/s at the acquisition hour): wide dark patches that
            fool SAR-only detectors.
  algae   — seasonal bloom regions (Baltic cyanobacteria, Gulf of Oman Noctiluca, Caribbean
            Sargassum): biogenic surface films that damp capillary waves like oil.
Chips use the same format as build_dataset.py. Any slick SkyTruth Cerulean published for that pass
is still rasterised as oil, so genuine spills in these regions stay positives.

    python -m training.mine_lookalikes --out ml_service/data/train_lookalikes --per-region 18
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
from shapely.geometry import Point, box

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from marinesight import cerulean  # noqa: E402
from marinesight.cdse import fetch_s1_vv_db, search  # noqa: E402
from marinesight.config import PIX_DEG  # noqa: E402
from marinesight.landmask import land_mask  # noqa: E402
from marinesight.metocean import metocean_at  # noqa: E402
from marinesight.raster import GeoRaster, snap_bbox  # noqa: E402

CHIP_PX = 512
REGIONS = [
    {"name": "ionian_sea", "kind": "lowwind", "bbox": [17.0, 35.5, 20.5, 38.5], "months": [6, 7, 8, 9]},
    {"name": "levantine", "kind": "lowwind", "bbox": [30.5, 32.5, 34.0, 35.0], "months": [6, 7, 8, 9]},
    {"name": "gulf_of_guinea", "kind": "lowwind", "bbox": [0.0, -3.0, 5.0, 3.0], "months": list(range(1, 13))},
    {"name": "andaman_sea", "kind": "lowwind", "bbox": [94.5, 7.5, 97.5, 12.0], "months": [3, 4, 10, 11]},
    {"name": "red_sea", "kind": "lowwind", "bbox": [37.0, 18.0, 40.0, 22.0], "months": [5, 6, 9, 10]},
    {"name": "baltic_bloom", "kind": "algae", "bbox": [17.5, 55.0, 21.0, 58.5], "months": [7, 8]},
    {"name": "gulf_of_oman_noctiluca", "kind": "algae", "bbox": [57.5, 23.0, 60.5, 25.5], "months": [2, 3]},
    {"name": "caribbean_sargassum", "kind": "algae", "bbox": [-66.0, 14.0, -60.0, 19.0], "months": [5, 6, 7, 8]},
]


def random_day(months: list[int], rng: random.Random) -> datetime:
    # Recent archive (Cerulean coverage is densest from mid-2025).
    for _ in range(100):
        year = rng.choice([2025, 2026])
        month = rng.choice(months)
        day = rng.randint(1, 28)
        d = datetime(year, month, day, tzinfo=timezone.utc)
        if datetime(2025, 3, 1, tzinfo=timezone.utc) <= d <= datetime(2026, 9, 10, tzinfo=timezone.utc):
            return d
    return datetime(2026, 7, 15, tzinfo=timezone.utc)


def try_chip(region: dict, rng: random.Random, out_dir: Path, idx: int) -> str:
    day = random_day(region["months"], rng)
    feats = search("sentinel-1-grd", region["bbox"], day, day + timedelta(days=1))
    feats = [f for f in feats if f["properties"].get("sar:instrument_mode") in (None, "IW")]
    if not feats:
        return "no scene"
    f = rng.choice(feats)
    from shapely.geometry import shape

    area = box(*region["bbox"]).intersection(shape(f["geometry"]))
    if area.is_empty or area.area < 0.3:
        return "small overlap"
    minx, miny, maxx, maxy = area.bounds
    half = CHIP_PX * PIX_DEG / 2
    for _ in range(20):
        cx, cy = rng.uniform(minx + half, maxx - half), rng.uniform(miny + half, maxy - half)
        if area.contains(Point(cx, cy)):
            break
    else:
        return "no interior point"
    acquired = datetime.fromisoformat(f["properties"]["datetime"].replace("Z", "+00:00"))
    met = metocean_at(cy, cx, acquired)
    wind = met.get("windMs")
    if region["kind"] == "lowwind" and (wind is None or wind >= 3.5):
        return f"wind {wind}"
    w, s = snap_bbox([cx - half, cy - half, cx - half, cy - half])[:2]
    bbox = [w, s, w + CHIP_PX * PIX_DEG, s + CHIP_PX * PIX_DEG]
    vv = fetch_s1_vv_db(bbox, f["properties"]["datetime"])
    valid = np.isfinite(vv.data)
    grid = GeoRaster(vv.data, vv.bbox)
    land = land_mask(grid)
    if valid.mean() < 0.5 or land.mean() > 0.3:
        return "invalid/land"
    t0 = (acquired - timedelta(minutes=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    t1 = (acquired + timedelta(minutes=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    labels = cerulean.slicks(bbox=bbox, start=t0, end=t1, limit=200,
                             properties="id,s1_scene_id,slick_timestamp,machine_confidence,cls,hitl_cls")
    oil = [l["geometry"] for l in labels if l.get("hitl_cls") in (None, *cerulean.HITL_OIL)]
    ambiguous = [l["geometry"] for l in labels if l.get("hitl_cls") == 9]
    mask = grid.rasterize(oil)
    label = mask.astype(np.uint8)
    label[grid.rasterize(ambiguous, all_touched=True).astype(bool)] = 255
    name = f"lookalike_{region['name']}_{idx:03d}"
    np.savez_compressed(out_dir / f"{name}.npz", vv_db=np.nan_to_num(vv.data, nan=0.0).astype(np.float16),
                        valid=valid, label=label, land=land)
    meta = {"scene": f["id"], "timestamp": f["properties"]["datetime"], "bbox": bbox, "kind": region["kind"],
            "region": region["name"], "windMs": wind, "label_ids": [l["id"] for l in labels],
            "oil_frac": float(mask.mean())}
    (out_dir / f"{name}.json").write_text(json.dumps(meta))
    return f"ok {name} wind={wind} cerulean_slicks={len(labels)}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(Path(__file__).resolve().parents[1] / "data" / "train_lookalikes"))
    ap.add_argument("--per-region", type=int, default=18)
    ap.add_argument("--max-tries", type=int, default=60)
    ap.add_argument("--seed", type=int, default=21)
    ns = ap.parse_args()
    out = Path(ns.out)
    out.mkdir(parents=True, exist_ok=True)
    for r_i, region in enumerate(REGIONS):
        rng = random.Random(ns.seed + r_i)
        got = len(list(out.glob(f"lookalike_{region['name']}_*.npz")))
        tries = 0
        while got < ns.per_region and tries < ns.max_tries:
            tries += 1
            try:
                msg = try_chip(region, rng, out, got)
            except Exception as exc:  # network hiccups must not end the mining run
                msg = f"error {exc}"
            if msg.startswith("ok"):
                got += 1
            print(f"[mine] {region['name']} {got}/{ns.per_region} (try {tries}): {msg}", flush=True)
    print("[mine] DONE", flush=True)


if __name__ == "__main__":
    main()
