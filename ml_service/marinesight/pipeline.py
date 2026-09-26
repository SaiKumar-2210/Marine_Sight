"""End-to-end scan for one UTC date:

  Sentinel-1 scenes over each monitored AOI
    -> U-Net segmentation -> exact slick polygons (raster-to-vector)
    -> ONLY for flagged candidates: Sentinel-2 + MetOcean verification -> P(oil)
    -> AIS drift back-propagation + SAR vessel detection -> ranked culprit
"""
from __future__ import annotations

import json
import math
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image, ImageDraw
from shapely.geometry import box

from .attribution import attribute
from .cdse import CdseError, _parse_iso, fetch_s1_vv_db, s1_scenes
from .config import ML_ROOT, QUICKLOOK_DIR, UNET_WEIGHTS, VERIFIER_MODEL
from .landmask import land_mask
from .metocean import metocean_at
from .optical import optical_features
from .raster import GeoRaster
from .segment import extract_slicks, scene_dark_fraction
from .unet import SlickSegmenter
from .verifier import Verifier, feature_vector, indicators
from .vessels_sar import detect_sar_vessels, static_targets

AOIS = json.loads((ML_ROOT / "aois.json").read_text(encoding="utf-8"))
MIN_SEA_FRACTION = 0.05


def emit(event: str, **payload) -> None:
    """JSON-lines progress on stderr; the Node job runner relays these to the UI."""
    sys.stderr.write(json.dumps({"event": event, **payload}) + "\n")
    sys.stderr.flush()


def load_tracks(ais_db: Optional[str], bbox: list[float], t0: datetime, t1: datetime) -> list[dict]:
    if not ais_db or not Path(ais_db).exists():
        return []
    con = sqlite3.connect(f"file:{ais_db}?mode=ro", uri=True, timeout=30)
    try:
        rows = con.execute(
            """SELECT p.mmsi, p.ts, p.lat, p.lon, p.sog, p.cog, v.name, v.ship_type, v.flag
               FROM ais_positions p LEFT JOIN vessels v ON v.mmsi = p.mmsi
               WHERE p.ts BETWEEN ? AND ? AND p.lat BETWEEN ? AND ? AND p.lon BETWEEN ? AND ?
               ORDER BY p.mmsi, p.ts""",
            (int(t0.timestamp()), int(t1.timestamp()), bbox[1], bbox[3], bbox[0], bbox[2]),
        ).fetchall()
    except sqlite3.Error as exc:
        emit("warning", message=f"AIS history unavailable: {exc}")
        return []
    finally:
        con.close()
    tracks: dict[str, dict] = {}
    for mmsi, ts, lat, lon, sog, cog, name, stype, flag in rows:
        tr = tracks.setdefault(mmsi, {"mmsi": mmsi, "name": name, "shipType": stype, "flag": flag, "points": []})
        tr["points"].append({"ts": datetime.fromtimestamp(ts, timezone.utc), "lat": lat, "lon": lon,
                             "sog": sog, "cog": cog})
    return list(tracks.values())


def _quicklook_s1(vv: GeoRaster, slick, sar_vessels: list[dict], path: Path, pad_frac: float = 0.25) -> list[float]:
    r0, c0, r1, c1 = slick.window
    ph, pw = int((r1 - r0) * pad_frac) + 20, int((c1 - c0) * pad_frac) + 20
    R0, C0 = max(0, r0 - ph), max(0, c0 - pw)
    R1, C1 = min(vv.height, r1 + ph), min(vv.width, c1 + pw)
    crop = vv.crop(R0, C0, R1, C1)
    g = np.nan_to_num(np.clip((crop.data + 28.0) / 22.0, 0, 1), nan=0.0) * 255
    img = Image.fromarray(g.astype(np.uint8)).convert("RGB")
    scale = max(1.0, 900 / max(img.size))
    img = img.resize((int(img.width * scale), int(img.height * scale)), Image.NEAREST)
    draw = ImageDraw.Draw(img)

    def px(lon, lat):
        c, r = crop.lonlat_to_pixel(lon, lat)
        return float(c) * scale, float(r) * scale

    for poly in slick.geometry.geoms:
        for ring in [poly.exterior, *poly.interiors]:
            draw.line([px(x, y) for x, y in ring.coords], fill=(255, 70, 40), width=2)
    for v in sar_vessels:
        if crop.bbox[0] <= v["lon"] <= crop.bbox[2] and crop.bbox[1] <= v["lat"] <= crop.bbox[3]:
            x, y = px(v["lon"], v["lat"])
            color = (190, 190, 190) if v.get("static") else (60, 220, 255)  # grey = island / structure
            draw.ellipse([x - 7, y - 7, x + 7, y + 7], outline=color, width=2)
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, optimize=True)
    return crop.bbox


def _quicklook_rgb(rgb: np.ndarray, path: Path) -> None:
    img = Image.fromarray(rgb)
    scale = max(1.0, 700 / max(img.size))
    img = img.resize((int(img.width * scale), int(img.height * scale)), Image.BILINEAR)
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, optimize=True)


def _group_passes(scenes: list[dict]) -> list[dict]:
    """Consecutive frames of one satellite pass (seconds apart) become a single acquisition, so a
    slick crossing a frame boundary is segmented whole. The fetch window (±2 min around the first
    frame) mosaics all frames of the pass."""
    from shapely.ops import unary_union

    passes: list[dict] = []
    for sc in sorted(scenes, key=lambda s: s["datetime"]):
        t = _parse_iso(sc["datetime"])
        last = passes[-1] if passes else None
        if last and last["platform"] == sc["platform"] and (t - last["_t_end"]).total_seconds() <= 90:
            last["footprint"] = unary_union([last["footprint"], sc["footprint"]])
            last["frames"].append(sc["id"])
            last["_t_end"] = t
            continue
        passes.append({**sc, "frames": [sc["id"]], "_t_end": t})
    for p in passes:
        p.pop("_t_end")
    return passes


def _status(p: float, thresholds: dict) -> str:
    if p >= thresholds["confirm"]:
        return "confirmed"
    if p >= thresholds["review"]:
        return "review"
    return "rejected"


def _severity(status: str, p: float, area_km2: float) -> str:
    if status == "confirmed" and (p >= 0.8 or area_km2 >= 10):
        return "HIGH"
    if status in ("confirmed", "review"):
        return "REVIEW"
    return "LOW"


def scan(date: str, aoi_ids: Optional[list[str]] = None, ais_db: Optional[str] = None,
         bbox_override: Optional[list[float]] = None) -> dict:
    t_start = time.time()
    if not UNET_WEIGHTS.exists():
        raise RuntimeError(f"U-Net weights missing at {UNET_WEIGHTS} — run training/train_unet.py")
    if not VERIFIER_MODEL.exists():
        raise RuntimeError(f"Verifier model missing at {VERIFIER_MODEL} — run training/train_verifier.py")
    seg = SlickSegmenter(UNET_WEIGHTS)
    verifier = Verifier()
    aois = [a for a in AOIS if not aoi_ids or a["id"] in aoi_ids]
    if bbox_override:
        aois = [{"id": "CUSTOM", "name": "Custom area", "bbox": bbox_override}]
    stats = {"aois": len(aois), "scenes": 0, "candidates": 0, "verified": 0, "confirmed": 0, "review": 0,
             "rejected": 0, "skipped": []}
    spills: list[dict] = []
    for ai, aoi in enumerate(aois):
        base_pct = 100.0 * ai / len(aois)
        span = 100.0 / len(aois)
        emit("progress", stage="catalog", aoi=aoi["id"], pct=round(base_pct, 1),
             message=f"Searching Sentinel-1 passes over {aoi['name']}")
        try:
            scenes = s1_scenes(aoi["bbox"], date)
        except CdseError as exc:
            stats["skipped"].append({"aoi": aoi["id"], "reason": str(exc)})
            emit("warning", message=str(exc))
            continue
        if not scenes:
            stats["skipped"].append({"aoi": aoi["id"], "reason": "no Sentinel-1 pass on this date"})
            continue
        aoi_box = box(*aoi["bbox"])
        scenes = _group_passes(scenes)
        for si, sc in enumerate(scenes):
            region = aoi_box.intersection(sc["footprint"])
            if region.is_empty or region.area < 0.02:
                continue
            acquired = _parse_iso(sc["datetime"])
            spct = base_pct + span * si / len(scenes)
            emit("progress", stage="sar_fetch", aoi=aoi["id"], scene=sc["id"], pct=round(spct, 1),
                 message=f"Fetching Sentinel-1 VV backscatter ({sc['id'][:3]} {sc['datetime'][11:16]} UTC)")
            try:
                vv = fetch_s1_vv_db(list(region.bounds), sc["datetime"])
            except CdseError as exc:
                stats["skipped"].append({"aoi": aoi["id"], "scene": sc["id"], "reason": str(exc)})
                emit("warning", message=str(exc))
                continue
            invalid = ~np.isfinite(vv.data) | land_mask(vv)
            if (~invalid).mean() < MIN_SEA_FRACTION:
                continue
            stats["scenes"] += 1
            emit("progress", stage="segmentation", aoi=aoi["id"], pct=round(spct + span * 0.2 / len(scenes), 1),
                 message=f"U-Net segmentation of {vv.height}x{vv.width} px")
            prob = seg.predict(seg.prep(vv.data, invalid))
            slicks = extract_slicks(prob, vv, invalid, seg.threshold, low=seg.low_threshold)
            sar_vessels = detect_sar_vessels(vv, invalid)
            dark_frac = scene_dark_fraction(vv.data, invalid)
            stats["candidates"] += len(slicks)
            emit("progress", stage="verification", aoi=aoi["id"], pct=round(spct + span * 0.5 / len(scenes), 1),
                 message=f"{len(slicks)} SAR candidate(s) → Sentinel-2 + MetOcean verification")
            for slick in slicks:
                spills.append(_verify_and_attribute(date, aoi, sc, acquired, vv, slick, sar_vessels, dark_frac,
                                                    verifier, ais_db))
                stats["verified"] += 1
    # Deduplicate detections of the same slick from overlapping passes/AOIs.
    spills.sort(key=lambda s: s["oilProbability"], reverse=True)
    kept: list[dict] = []
    for s in spills:
        g = s.pop("_geom")
        if any(g.intersects(k["_g"]) and g.intersection(k["_g"]).area > 0.3 * min(g.area, k["_g"].area) for k in kept):
            for ql in s["quicklooks"].values():
                Path(ql["file"]).unlink(missing_ok=True)
            continue
        s["_g"] = g
        kept.append(s)
    per_aoi: dict[str, int] = {}
    for s in sorted(kept, key=lambda s: (s["aoiId"], -s["areaKm2"])):
        per_aoi[s["aoiId"]] = per_aoi.get(s["aoiId"], 0) + 1
        s["id"] = f"MS-{date.replace('-', '')}-{s['aoiId'][:10]}-{per_aoi[s['aoiId']]:02d}"
        s.pop("_g")
        for key in ("s1", "s2"):
            tmp = s["quicklooks"].get(key)
            if tmp:
                final = QUICKLOOK_DIR / f"{s['id']}_{key}.png"
                Path(tmp["file"]).replace(final)
                tmp["file"] = final.name
        stats[s["status"]] += 1
    stats["elapsedSec"] = round(time.time() - t_start, 1)
    emit("progress", stage="done", pct=100, message=f"Scan complete: {stats['confirmed']} confirmed, "
                                                     f"{stats['review']} for review, {stats['rejected']} rejected")
    return {
        "ok": True, "date": date, "aois": [a["id"] for a in aois], "stats": stats,
        "models": {"unet": {k: v for k, v in seg.meta.items() if k in ("threshold", "validation", "labels")},
                   "verifier": {k: v for k, v in verifier.meta.items() if k in ("cv", "n_samples", "thresholds")}},
        "spills": kept,
    }


def _verify_and_attribute(date, aoi, scene, acquired, vv, slick, sar_vessels, dark_frac, verifier, ais_db) -> dict:
    m = slick.metrics
    lon, lat = m["centroid"]
    met = metocean_at(lat, lon, acquired)
    try:
        opt_feats, opt_info = optical_features(slick.geometry, acquired, met, want_image=True)
    except CdseError as exc:
        from .optical import EMPTY

        opt_feats, opt_info = dict(EMPTY), {"status": "error", "note": str(exc)}
    nearest = None
    if m.get("endpoints") and sar_vessels:
        kx = 111.32 * math.cos(math.radians(lat))
        nearest = min((math.hypot((v["lon"] - e[0]) * kx, (v["lat"] - e[1]) * 111.32)
                      for v in sar_vessels if not v.get("static") for e in m["endpoints"]), default=None)
    feats = feature_vector(m, dark_frac, nearest, met, opt_feats)
    p_oil = verifier.predict(feats)
    status = _status(p_oil, verifier.thresholds)

    attribution = None
    if status != "rejected":
        minx, miny, maxx, maxy = slick.geometry.bounds
        # Temporal persistence check (islands / platforms vs ships) for SAR targets around this slick.
        ends = m.get("endpoints") or [m["centroid"]]
        kx = 111.32 * math.cos(math.radians(lat))
        end_km = lambda v: min(math.hypot((v["lon"] - e[0]) * kx, (v["lat"] - e[1]) * 111.32) for e in ends)  # noqa: E731
        # Targets near the slick ends, incl. up to ~60 km ahead of them (recent-vessel candidates).
        around = sorted((v for v in sar_vessels if v.get("static") is None and end_km(v) < 60.0), key=end_km)[:16]
        emit("progress", stage="attribution", aoi=aoi["id"], message=f"Attribution: checking {len(around)} SAR target(s) against earlier passes")
        static_targets(around, acquired)
        buf = 0.5
        tracks = load_tracks(ais_db, [minx - buf, miny - buf, maxx + buf, maxy + buf],
                             acquired - timedelta(hours=24), acquired + timedelta(hours=1))
        rows, cols = np.nonzero(slick.pixel_mask)
        r0, c0 = slick.window[:2]
        plon, plat = vv.pixel_to_lonlat(cols + c0 + 0.5, rows + r0 + 0.5)
        attribution = attribute(slick.geometry, np.column_stack([plon, plat]), m, acquired, met, tracks, sar_vessels)

    tmp_id = f"tmp_{scene['id'][:40]}_{int(lon * 1e4)}_{int(lat * 1e4)}"
    quicklooks = {}
    s1_path = QUICKLOOK_DIR / f"{tmp_id}_s1.png"
    ql_bbox = _quicklook_s1(vv, slick, sar_vessels, s1_path)
    quicklooks["s1"] = {"file": str(s1_path), "bbox": ql_bbox}
    if opt_info.get("rgb") is not None:
        s2_path = QUICKLOOK_DIR / f"{tmp_id}_s2.png"
        _quicklook_rgb(opt_info.pop("rgb"), s2_path)
        quicklooks["s2"] = {"file": str(s2_path), "bbox": opt_info.pop("bbox")}

    culprit = (attribution or {}).get("culprit")
    return {
        "_geom": slick.geometry,
        "date": date,
        "aoiId": aoi["id"],
        "aoiName": aoi["name"],
        "scene": {"id": scene["id"], "acquiredAt": scene["datetime"], "platform": scene["platform"],
                  "orbitState": scene.get("orbitState")},
        "geometry": slick.geojson(),
        "centroid": {"lon": lon, "lat": lat},
        "areaKm2": m["areaKm2"],
        "lengthKm": m["lengthKm"],
        "metrics": {k: v for k, v in m.items() if k not in ("centroid",)},
        "oilProbability": round(p_oil, 4),
        "status": status,
        "severity": _severity(status, p_oil, m["areaKm2"]),
        "verification": {
            "features": {k: (None if isinstance(v, float) and math.isnan(v) else v) for k, v in feats.items()},
            "metocean": met,
            "optical": opt_info,
            "indicators": indicators(feats, opt_info),
        },
        "sarVesselsNearby": [v for v in sar_vessels if abs(v["lon"] - lon) < 0.6 and abs(v["lat"] - lat) < 0.6][:50],
        "attribution": attribution,
        "culprit": culprit,
        "quicklooks": quicklooks,
    }
