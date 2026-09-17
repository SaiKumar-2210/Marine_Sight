"""
Oil-spill workflow
  1. Sentinel-1 SAR  — find dark surface slicks
  2. Sentinel-2      — optical confirm / cloud / reject
  3. Metocean        — wind-window + waves + currents
  4. Fusion          — confirm / review / reject
  5. AIS / platforms — likely cause
"""
from __future__ import annotations

import json
import os
import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image

from .ais import attribute_source
from .cnn import SarOilClassifier
from .copernicus import fetch_sentinel1
from .fusion import fuse
from .geo import bbox_center, pad_bbox, split_bbox_grid
from .metocean import fetch_metocean
from .optical import confirm_optical
from .sar import detect_dark_slicks

# Tight AOIs over tanker lanes / platform belts rather than whole EEZ (rate limits).
COAST_AOIS = {
    "ARABIAN_SEA": [
        [71.55, 18.35, 71.95, 18.75],
        [71.90, 18.70, 72.30, 19.10],
        [71.20, 19.20, 71.60, 19.60],
        [72.20, 18.90, 72.60, 19.30],
    ],
    "GULF_OF_MEXICO": [
        [-93.70, 27.40, -93.30, 27.80],
        [-92.60, 28.00, -92.20, 28.40],
        [-91.20, 27.20, -90.80, 27.60],
        [-94.20, 28.20, -93.80, 28.60],
    ],
    "MALACCA_STRAIT": [
        [103.60, 1.10, 104.00, 1.50],
        [103.20, 1.40, 103.60, 1.80],
        [101.70, 2.00, 102.10, 2.40],
        [104.10, 1.20, 104.50, 1.60],
    ],
    "PERSIAN_GULF": [
        [56.10, 26.20, 56.50, 26.60],
        [53.45, 24.65, 53.85, 25.05],
        [54.80, 25.10, 55.20, 25.50],
        [55.90, 26.50, 56.30, 26.90],
    ],
    "NORTH_SEA": [
        [0.70, 57.50, 1.10, 57.90],
        [3.00, 56.35, 3.40, 56.75],
        [1.80, 56.80, 2.20, 57.20],
        [0.20, 56.40, 0.60, 56.80],
    ],
}


def log(msg: str) -> None:
    sys.stderr.write(msg + "\n")
    sys.stderr.flush()


def load_dotenv(project_root: Path) -> None:
    env_path = project_root / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


def _save_chip(arr: Optional[np.ndarray], path: Path) -> Optional[str]:
    if arr is None:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    if arr.ndim == 2:
        img = Image.fromarray(arr.astype(np.uint8), mode="L")
    else:
        img = Image.fromarray(arr[:, :, :3].astype(np.uint8), mode="RGB")
    img.save(path)
    return str(path)


def _synthetic_s1(width: int = 512, height: int = 512) -> np.ndarray:
    rng = np.random.default_rng(7)
    sea = rng.normal(168, 10, (height, width)).clip(90, 230)
    yy, xx = np.ogrid[:height, :width]
    # Elongated dark trail (ship discharge analogue).
    cx, cy = 240, 250
    dx, dy = xx - cx, yy - cy
    rot_x = dx * 0.92 + dy * 0.39
    rot_y = -dx * 0.39 + dy * 0.92
    slick = ((rot_x / 110.0) ** 2 + (rot_y / 14.0) ** 2) <= 1.0
    sea[slick] = rng.normal(58, 8, int(slick.sum())).clip(25, 90)
    rgb = np.stack([sea, sea * 0.85, sea], axis=-1).clip(0, 255).astype(np.uint8)
    return rgb


def _aois_for(coast_id: str, bbox: list[float], mode: str, lat: Optional[float], lng: Optional[float]) -> list[list[float]]:
    if mode == "point" and lat is not None and lng is not None:
        return [[lng - 0.12, lat - 0.12, lng + 0.12, lat + 0.12]]
    aois = COAST_AOIS.get(coast_id)
    if aois:
        return aois
    return split_bbox_grid(bbox, 2, 2)


def _signals(sar, optical, metocean, ais, fusion) -> list:
    opt_score = optical.get("score")
    opt_txt = "—" if opt_score is None else f"{opt_score}"
    return [
        ["Sentinel-1 SAR dark-slick", sar.get("sarScore"), sar_note(sar)],
        ["Sentinel-2 optical", opt_txt if opt_score is None else opt_score, optical.get("note") or optical.get("status")],
        ["Metocean wind / waves", metocean.get("score"), metocean.get("note")],
        ["AIS / platform source", (ais or {}).get("score") or 28, (ais or {}).get("causeLabel") or "unattributed"],
    ]


def sar_note(sar: dict) -> str:
    bits = [f"contrast {sar.get('contrast')}", f"elongation {sar.get('elongation')}"]
    if sar.get("cnnProbability") is not None:
        bits.append(f"CNN p(oil)={sar['cnnProbability']:.2f}")
    bits.append(f"area {sar.get('areaKm2')} km²")
    return " · ".join(bits)


def _timeline(date_str: str, fusion, optical, metocean, ais) -> list:
    t0 = "00:00"
    events = [
        [t0, "SAR candidate extracted", f"Sentinel-1 VV dark patch scored {fusion['confidence']}% composite after gates."],
        [t0, f"Sentinel-2 {optical.get('status')}", optical.get("note") or ""],
        [t0, f"Metocean window {metocean.get('window')}", metocean.get("note") or ""],
        [t0, f"Fusion → {fusion['status']}", "; ".join(fusion.get("reasons") or [])],
        [t0, f"Cause: {(ais or {}).get('causeType') or 'unknown'}", (ais or {}).get("causeLabel") or ""],
    ]
    return events


def _to_incident(idx: int, coast: dict, date_str: str, sar, optical, metocean, ais, fusion, chips) -> dict:
    src = (ais or {}).get("source") or {}
    name = src.get("name") or "Unattributed"
    cause = (ais or {}).get("causeType") or "unknown"
    title_prefix = "Confirmed" if fusion["status"] == "confirmed" else "Review"
    now = datetime.utcnow().strftime("%H:%M")
    return {
        "id": f"INC-S1-{coast['id'][:6]}-{date_str.replace('-', '')}-{idx:02d}",
        "severity": fusion["severity"],
        "title": f"{title_prefix} SAR slick — {coast.get('name') or coast['id']}",
        "time": now,
        "detected": f"Detected {date_str} · {now} UTC",
        "confidence": fusion["confidence"],
        "area": f"{sar['areaKm2']} km²",
        "source": name,
        "mmsi": src.get("mmsi") or "",
        "distance": f"{src.get('distanceKm', '—')} km" if src else "—",
        "lat": sar["lat"],
        "lng": sar["lng"],
        "summary": f"{name} · {(ais or {}).get('causeLabel') or 'source unknown'}",
        "windDir": metocean.get("windDirDeg") or 0,
        "windSpeed": metocean.get("windKnots") or 0,
        "currentDir": metocean.get("currentDirDeg") or 0,
        "currentSpeed": metocean.get("currentKnots") or 0,
        "signals": _signals(sar, optical, metocean, ais, fusion),
        "polygon": sar["polygon"],
        "confirmationStatus": fusion["status"],
        "causeType": cause,
        "causeLabel": (ais or {}).get("causeLabel"),
        "timeline": _timeline(date_str, fusion, optical, metocean, ais),
        "chips": chips,
        "pipeline": {
            "sar": {k: v for k, v in sar.items() if k != "chip"},
            "optical": {k: v for k, v in optical.items() if k != "image"},
            "metocean": metocean,
            "ais": ais,
            "fusion": fusion,
        },
    }


def run_workflow(args: dict) -> dict:
    coast = {
        "id": args.get("coastId") or "ARABIAN_SEA",
        "name": args.get("coastName") or args.get("coastId") or "Coast",
    }
    date_str = args.get("date") or datetime.utcnow().strftime("%Y-%m-%d")
    bbox = args.get("bbox") or [69.0, 15.0, 75.0, 21.0]
    mode = args.get("mode") or "scan"
    dry = bool(args.get("dryRun"))
    vessels = args.get("vessels") or []
    cache_dir = Path(args["cacheDir"]) if args.get("cacheDir") else None
    scan_id = args.get("scanId") or f"{coast['id']}-{date_str}"

    model_dir = Path(args.get("modelDir") or (Path(__file__).resolve().parents[1]))
    cnn = SarOilClassifier(str(model_dir))
    log(f"[workflow] CNN backend={cnn.backend}  mode={mode}  dry={dry}  date={date_str}  coast={coast['id']}")

    aois = _aois_for(coast["id"], bbox, mode, args.get("lat"), args.get("lng"))
    stages = {
        "sar": {"tiles": 0, "candidates": 0, "skipped": []},
        "optical": {"confirmed": 0, "cloudy": 0, "rejected": 0, "other": 0},
        "metocean": {"valid": 0, "marginal": 0, "fail": 0},
        "fusion": {"confirmed": 0, "review": 0, "rejected": 0},
        "ais": {"attributed": 0, "unknown": 0},
    }

    raw_candidates = []
    for i, aoi in enumerate(aois):
        log(f"[workflow] SAR tile {i+1}/{len(aois)} bbox={aoi}")
        try:
            tile = _synthetic_s1() if dry else fetch_sentinel1(aoi, date_str, size=512)
        except Exception as exc:
            log(f"[workflow] SAR fetch failed: {exc}")
            stages["sar"]["skipped"].append({"aoi": aoi, "reason": str(exc)})
            continue
        stages["sar"]["tiles"] += 1
        if tile is None:
            stages["sar"]["skipped"].append({"aoi": aoi, "reason": "no_s1_scene"})
            log("[workflow] no Sentinel-1 coverage in window")
            continue
        detected = detect_dark_slicks(tile, aoi, cnn=cnn, max_candidates=3)
        if detected.get("skipped"):
            stages["sar"]["skipped"].append({"aoi": aoi, "reason": detected["skipped"]})
            log(f"[workflow] tile skipped ({detected['skipped']}) darkFrac={detected.get('darkFraction')}")
            continue
        for cand in detected["candidates"]:
            cand["tileBbox"] = aoi
            raw_candidates.append(cand)

    # Point mode: if CFAR found nothing, still score the center chip so analyze-aoi has a real answer.
    if mode == "point" and not raw_candidates and not dry:
        lat, lng = float(args["lat"]), float(args["lng"])
        aoi = aois[0]
        try:
            tile = fetch_sentinel1(aoi, date_str, size=512)
        except Exception:
            tile = None
        if tile is not None:
            cnn_p = cnn.predict_array(tile)
            raw_candidates.append({
                "lat": lat,
                "lng": lng,
                "areaKm2": 0.0,
                "elongation": 1.0,
                "contrast": 0.0,
                "cnnProbability": None if cnn_p is None else round(float(cnn_p), 4),
                "physicalScore": 0.0,
                "sarScore": round((cnn_p or 0) * 100.0, 1),
                "polygon": [
                    [lat + 0.02, lng - 0.03],
                    [lat + 0.01, lng + 0.04],
                    [lat - 0.02, lng + 0.02],
                    [lat - 0.01, lng - 0.03],
                    [lat + 0.02, lng - 0.03],
                ],
                "chipBbox": aoi,
                "chip": tile,
                "pointFallback": True,
            })

    raw_candidates.sort(key=lambda c: c["sarScore"], reverse=True)
    raw_candidates = raw_candidates[:8]
    stages["sar"]["candidates"] = len(raw_candidates)
    log(f"[workflow] SAR candidates kept: {len(raw_candidates)}")

    incidents = []
    rejected = []
    idx = 1
    for cand in raw_candidates:
        chip = cand.pop("chip", None)
        log(f"[workflow] optical+metocean @ {cand['lat']},{cand['lng']}")
        if dry:
            optical = {
                "status": "confirmed",
                "score": 72.0,
                "cloudFraction": 8.0,
                "note": "Dry-run optical confirmation",
                "image": None,
            }
            metocean = {
                "source": "dry-run",
                "windMs": 6.2,
                "windKnots": 12.1,
                "windDirDeg": 315,
                "windDir": "NW",
                "waveHeightM": 1.1,
                "currentMs": 0.31,
                "currentKnots": 0.6,
                "currentDirDeg": 140,
                "currentDir": "SE",
                "driftKnots": 0.96,
                "window": "valid",
                "score": 88.0,
                "note": "Wind 6.2 m/s inside SAR oil window (2.5–14 m/s)",
                "formula": "V_slick = V_current + 0.03 * V_wind",
            }
        else:
            try:
                optical = confirm_optical(cand, date_str)
            except Exception as exc:
                log(f"[workflow] optical failed: {exc}")
                optical = {"status": "no_scene", "score": None, "cloudFraction": None, "note": str(exc), "image": None}
            try:
                metocean = fetch_metocean(cand["lat"], cand["lng"], date_str)
            except Exception as exc:
                log(f"[workflow] metocean failed: {exc}")
                metocean = {"window": "unknown", "score": 50.0, "note": str(exc), "formula": "V_slick = V_current + 0.03 * V_wind"}

        opt_status = optical.get("status")
        if opt_status == "confirmed":
            stages["optical"]["confirmed"] += 1
        elif opt_status == "cloudy":
            stages["optical"]["cloudy"] += 1
        elif opt_status == "rejected":
            stages["optical"]["rejected"] += 1
        else:
            stages["optical"]["other"] += 1
        stages["metocean"][metocean.get("window") if metocean.get("window") in ("valid", "marginal", "fail") else "marginal"] = (
            stages["metocean"].get(metocean.get("window") if metocean.get("window") in ("valid", "marginal", "fail") else "marginal", 0) + 1
        )

        ais = attribute_source(cand, metocean, vessels, coast["id"])
        if ais.get("causeType") == "unknown":
            stages["ais"]["unknown"] += 1
        else:
            stages["ais"]["attributed"] += 1

        fusion = fuse(cand, optical, metocean, ais)
        stages["fusion"][fusion["status"]] = stages["fusion"].get(fusion["status"], 0) + 1

        chips = {}
        if cache_dir is not None:
            if chip is not None:
                chips["s1"] = _save_chip(chip, cache_dir / scan_id / f"s1_{idx:02d}.png")
            if optical.get("image") is not None:
                chips["s2"] = _save_chip(optical.get("image"), cache_dir / scan_id / f"s2_{idx:02d}.png")

        record = _to_incident(idx, coast, date_str, cand, optical, metocean, ais, fusion, chips)
        if fusion["status"] == "rejected":
            rejected.append(record)
        else:
            incidents.append(record)
            idx += 1

    return {
        "ok": True,
        "mode": mode,
        "dryRun": dry,
        "date": date_str,
        "coastId": coast["id"],
        "cnnBackend": cnn.backend,
        "stages": stages,
        "incidents": incidents,
        "rejected": rejected,
        "scanId": scan_id,
    }


def main(argv: Optional[list[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="MarineSight oil-spill workflow")
    parser.add_argument("--date", required=True)
    parser.add_argument("--coast-id", default="ARABIAN_SEA")
    parser.add_argument("--coast-name", default="")
    parser.add_argument("--bbox", default="69,15,75,21")
    parser.add_argument("--ais", default="")
    parser.add_argument("--out", default="")
    parser.add_argument("--cache-dir", default="")
    parser.add_argument("--model-dir", default="")
    parser.add_argument("--mode", default="scan", choices=["scan", "point"])
    parser.add_argument("--lat", type=float, default=None)
    parser.add_argument("--lng", type=float, default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--scan-id", default="")
    ns = parser.parse_args(argv)

    here = Path(__file__).resolve()
    project_root = here.parents[2]
    load_dotenv(project_root)

    bbox = [float(x) for x in ns.bbox.split(",")]
    vessels = []
    if ns.ais:
        with open(ns.ais, "r", encoding="utf-8") as f:
            payload = json.load(f)
            vessels = payload.get("vessels", payload if isinstance(payload, list) else [])

    args = {
        "date": ns.date,
        "coastId": ns.coast_id,
        "coastName": ns.coast_name or ns.coast_id,
        "bbox": bbox,
        "vessels": vessels,
        "mode": ns.mode,
        "lat": ns.lat,
        "lng": ns.lng,
        "dryRun": ns.dry_run,
        "cacheDir": ns.cache_dir or str(project_root / "ml_service" / "cache"),
        "modelDir": ns.model_dir or str(project_root / "ml_service"),
        "scanId": ns.scan_id or f"{ns.coast_id}-{ns.date}",
    }
    try:
        result = run_workflow(args)
    except Exception as exc:
        result = {"ok": False, "error": str(exc), "trace": traceback.format_exc()}
        log(result["trace"])

    text = json.dumps(result, indent=2)
    if ns.out:
        Path(ns.out).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
