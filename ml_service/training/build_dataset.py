"""Build the U-Net training set from real Sentinel-1 chips + SkyTruth Cerulean slick polygons.

Positives: Cerulean slicks confirmed by human review (hitl_cls oil classes) plus
high-confidence machine detections. Each sample is a 512x512 VV-dB chip (~37 km)
fetched from CDSE for the exact S1 pass, with every Cerulean slick in that scene
rasterised as the mask. Human-flagged "Ambiguous" slicks become ignore regions.
The Oman benchmark (2026-08-07) is excluded so it stays a held-out test.

    python -m training.build_dataset --out ml_service/data/train --max 360
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
from shapely.geometry import box

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from marinesight import cerulean  # noqa: E402
from marinesight.cdse import fetch_s1_vv_db  # noqa: E402
from marinesight.config import PIX_DEG  # noqa: E402
from marinesight.landmask import land_mask  # noqa: E402
from marinesight.raster import GeoRaster, snap_bbox  # noqa: E402

CHIP_PX = 512
HOLDOUT_BOX = box(53.0, 14.0, 61.0, 21.0)
HOLDOUT_DATES = ("2026-08-06", "2026-08-07", "2026-08-08")
PROPS = "id,s1_scene_id,slick_timestamp,machine_confidence,cls,hitl_cls,area"


def is_holdout(s: dict) -> bool:
    return s["slick_timestamp"][:10] in HOLDOUT_DATES and s["geometry"].intersects(HOLDOUT_BOX)


def collect_candidates(max_n: int, seed: int) -> list[dict]:
    rng = random.Random(seed)
    pool: dict[int, dict] = {}
    # Human-verified oil across the archive.
    for offset in range(0, 3000, 500):
        for s in cerulean.slicks(cql="hitl_cls > 1 AND hitl_cls < 9", limit=500, offset=offset, properties=PROPS):
            pool[s["id"]] = s
    print(f"[dataset] human-verified slicks: {len(pool)}", flush=True)
    # High-confidence machine detections from recent months (diversity of regions/seasons).
    months = [("2025-%02d" % m) for m in range(7, 13)] + [("2026-%02d" % m) for m in range(1, 9)]
    for mo in months:
        y, m = map(int, mo.split("-"))
        end = f"{y + (m == 12)}-{(m % 12) + 1:02d}-01T00:00:00Z"
        for s in cerulean.slicks(start=f"{mo}-01T00:00:00Z", end=end,
                                 cql="machine_confidence > 0.97 AND area > 1000000",
                                 limit=40, offset=rng.randint(0, 400), properties=PROPS):
            pool.setdefault(s["id"], s)
    cands = [s for s in pool.values() if not is_holdout(s) and s.get("s1_scene_id")]
    rng.shuffle(cands)
    # One chip per scene neighbourhood: skip slicks already covered by an earlier chip.
    chosen, covered = [], []
    for s in cands:
        c = s["geometry"].centroid
        if any(sc == s["s1_scene_id"] and abs(c.x - x) < 0.15 and abs(c.y - y) < 0.15 for sc, x, y in covered):
            continue
        covered.append((s["s1_scene_id"], c.x, c.y))
        chosen.append(s)
        if len(chosen) >= max_n:
            break
    print(f"[dataset] chips planned: {len(chosen)}", flush=True)
    return chosen


def collect_large(max_n: int, seed: int, existing: set) -> list[dict]:
    """Large (>30 km2) slicks — wide, weathered bodies the first training set under-represented."""
    rng = random.Random(seed)
    pool: dict[int, dict] = {}
    for s in cerulean.slicks(cql="hitl_cls > 1 AND hitl_cls < 9 AND area > 30000000", limit=500, properties=PROPS):
        pool[s["id"]] = s
    months = [("2025-%02d" % m) for m in range(6, 13)] + [("2026-%02d" % m) for m in range(1, 9)]
    for mo in months:
        y, m = map(int, mo.split("-"))
        end = f"{y + (m == 12)}-{(m % 12) + 1:02d}-01T00:00:00Z"
        for s in cerulean.slicks(start=f"{mo}-01T00:00:00Z", end=end, cql="machine_confidence > 0.9 AND area > 30000000",
                                 limit=30, offset=rng.randint(0, 60), properties=PROPS):
            pool.setdefault(s["id"], s)
    cands = [s for s in pool.values() if not is_holdout(s) and s.get("s1_scene_id") and s["id"] not in existing]
    rng.shuffle(cands)
    print(f"[dataset] large-slick candidates: {len(cands)}", flush=True)
    return cands[:max_n]


def build_chip(s: dict, out_dir: Path, rng: random.Random, background: bool = False) -> str:
    c = s["geometry"].centroid
    half = CHIP_PX * PIX_DEG / 2
    jitter = half * 0.5
    cx, cy = c.x + rng.uniform(-jitter, jitter), c.y + rng.uniform(-jitter, jitter)
    if background:
        # Same S1 pass, displaced ~50-90 km: mostly slick-free sea incl. natural look-alikes.
        ang, dist = rng.uniform(0, 6.283), rng.uniform(0.45, 0.8)
        cx, cy = c.x + dist * np.cos(ang), c.y + dist * np.sin(ang)
    w, sth = snap_bbox([cx - half, cy - half, cx - half, cy - half])[:2]
    bbox = [w, sth, w + CHIP_PX * PIX_DEG, sth + CHIP_PX * PIX_DEG]
    ts = s["slick_timestamp"] + "Z"
    vv = fetch_s1_vv_db(bbox, ts)
    valid = np.isfinite(vv.data)
    if valid.mean() < 0.3:
        return f"skip {s['id']} (valid {valid.mean():.2f})"
    labels = cerulean.scene_slicks(s["s1_scene_id"], bbox)
    oil = [l["geometry"] for l in labels if l.get("hitl_cls") in (None, *cerulean.HITL_OIL)]
    ambiguous = [l["geometry"] for l in labels if l.get("hitl_cls") == 9]
    grid = GeoRaster(vv.data, vv.bbox)
    mask = grid.rasterize(oil)
    ignore = grid.rasterize(ambiguous, all_touched=True)
    land = land_mask(grid)
    label = mask.astype(np.uint8)
    label[ignore.astype(bool)] = 255
    name = f"chip_{s['id']}" + ("_bg" if background else "")
    np.savez_compressed(
        out_dir / f"{name}.npz",
        vv_db=np.nan_to_num(vv.data, nan=0.0).astype(np.float16),
        valid=valid,
        label=label,
        land=land,
    )
    meta = {
        "slick_id": s["id"], "scene": s["s1_scene_id"], "timestamp": ts, "bbox": bbox,
        "label_ids": [l["id"] for l in labels], "hitl_cls": s.get("hitl_cls"),
        "oil_frac": float(mask.mean()), "land_frac": float(land.mean()), "background": background,
    }
    (out_dir / f"{name}.json").write_text(json.dumps(meta))
    return f"ok {name} oil={mask.mean():.3f} land={land.mean():.2f}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(Path(__file__).resolve().parents[1] / "data" / "train"))
    ap.add_argument("--max", type=int, default=360)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--seed", type=int, default=13)
    ap.add_argument("--bg-frac", type=float, default=0.3, help="extra displaced background chips per slick chip")
    ap.add_argument("--large", type=int, default=0, help="add N chips of large (>30 km2) slicks to an existing set")
    ns = ap.parse_args()
    out_dir = Path(ns.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    if ns.large:
        existing = {int(p.stem.split("_")[1]) for p in out_dir.glob("chip_*.npz")}
        chosen = collect_large(ns.large, ns.seed, existing)
        ns.bg_frac = 0.0
    else:
        chosen = collect_candidates(ns.max, ns.seed)
        (out_dir / "_plan.json").write_text(json.dumps([{"id": s["id"], "scene": s["s1_scene_id"]} for s in chosen]))
    done = 0
    with ThreadPoolExecutor(ns.workers) as pool:
        futs = {}
        for i, s in enumerate(chosen):
            if (out_dir / f"chip_{s['id']}.npz").exists():
                continue
            futs[pool.submit(build_chip, s, out_dir, random.Random(ns.seed + i))] = s
            if random.Random(ns.seed * 7 + i).random() < ns.bg_frac and not (out_dir / f"chip_{s['id']}_bg.npz").exists():
                futs[pool.submit(build_chip, s, out_dir, random.Random(ns.seed + 10_000 + i), True)] = s
        for fut in as_completed(futs):
            done += 1
            try:
                msg = fut.result()
            except Exception as exc:  # keep going; one bad chip must not stop the build
                msg = f"error {futs[fut]['id']}: {exc}"
                traceback.print_exc(limit=1)
            print(f"[dataset] {done}/{len(futs)} {msg}", flush=True)
    print("[dataset] DONE", flush=True)


if __name__ == "__main__":
    main()
