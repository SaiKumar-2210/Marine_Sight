"""Train the multi-modal false-positive filter (SAR + MetOcean + Sentinel-2 -> P(oil)).

For every training chip the trained U-Net proposes candidates exactly as in production.
Each candidate is labelled against SkyTruth Cerulean's published slicks for that pass:
  positive  — >=30 % of the candidate lies on a Cerulean oil slick
  negative  — no Cerulean slick within ~1 km (a look-alike Cerulean did not publish:
              low-wind patches, biogenic films, rain cells, internal waves, ...)
  skipped   — anything in between, or overlapping human-flagged 'ambiguous' areas.
Features are then gathered from all three modalities and a gradient-boosted classifier is
fitted with scene-grouped cross-validation (no scene in both train and test folds).

    python -m training.train_verifier
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import numpy as np
from scipy import ndimage

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from marinesight.config import UNET_WEIGHTS, VERIFIER_MODEL  # noqa: E402
from marinesight.metocean import metocean_at  # noqa: E402
from marinesight.optical import EMPTY, load_s2, optical_features  # noqa: E402
from marinesight.raster import GeoRaster  # noqa: E402
from marinesight.segment import extract_slicks, scene_dark_fraction  # noqa: E402
from marinesight.unet import SlickSegmenter  # noqa: E402
from marinesight.verifier import FEATURES, feature_vector  # noqa: E402
from marinesight.vessels_sar import detect_sar_vessels  # noqa: E402

_torch_lock = threading.Lock()


def chip_rows(npz: Path, seg: SlickSegmenter, use_s2: bool) -> list[dict]:
    meta = json.loads(npz.with_suffix(".json").read_text())
    d = np.load(npz)
    vv = d["vv_db"].astype(np.float32)
    valid = d["valid"]
    vv[~valid] = np.nan
    invalid = ~valid | d["land"]
    label = d["label"]
    grid = GeoRaster(vv, meta["bbox"])
    with _torch_lock:
        prob = seg.predict(seg.prep(vv, invalid))
    slicks = extract_slicks(prob, grid, invalid, seg.threshold, low=seg.low_threshold)
    if not slicks:
        return []
    oil = label == 1
    near_oil = ndimage.binary_dilation(oil, iterations=14)
    ignore = label == 255
    acquired = datetime.fromisoformat(meta["timestamp"].replace("Z", "+00:00"))
    lon_c, lat_c = (meta["bbox"][0] + meta["bbox"][2]) / 2, (meta["bbox"][1] + meta["bbox"][3]) / 2
    met = metocean_at(lat_c, lon_c, acquired)
    sar_vessels = detect_sar_vessels(grid, invalid)
    dark = scene_dark_fraction(vv, invalid)
    preloaded = None
    if use_s2:
        try:
            preloaded = load_s2(meta["bbox"], acquired, max_px=512)
        except Exception as exc:  # quota / transient errors: fall back to no optical
            print(f"[verifier] S2 unavailable for {npz.name}: {exc}", flush=True)
    rows = []
    for s in slicks:
        r0, c0, r1, c1 = s.window
        m = s.pixel_mask
        npx = m.sum()
        frac_oil = (m & oil[r0:r1, c0:c1]).sum() / npx
        if (m & ignore[r0:r1, c0:c1]).sum() / npx > 0.2:
            continue
        if frac_oil >= 0.3:
            y = 1
        elif not (m & near_oil[r0:r1, c0:c1]).any():
            y = 0
        else:
            continue
        if preloaded is not None and preloaded[0] is not None:
            opt, _ = optical_features(s.geometry, acquired, met, preloaded=preloaded)
        else:
            opt = dict(EMPTY)
        nearest = None
        if s.metrics.get("endpoints") and sar_vessels:
            kx = 111.32 * np.cos(np.radians(lat_c))
            nearest = min((np.hypot((v["lon"] - e[0]) * kx, (v["lat"] - e[1]) * 111.32)
                          for v in sar_vessels if not v.get("static") for e in s.metrics["endpoints"]), default=None)
        f = feature_vector(s.metrics, dark, nearest, met, opt)
        kind = meta.get("kind") or ("background" if meta.get("background") else "slick")
        f.update({"label": y, "scene": meta["scene"], "chip": npz.stem, "kind": kind, "oilFrac": round(float(frac_oil), 3)})
        rows.append(f)
    return rows


def fit(rows: list[dict], out: Path) -> dict:
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.inspection import permutation_importance
    from sklearn.metrics import average_precision_score, precision_recall_curve, roc_auc_score
    from sklearn.model_selection import GroupKFold
    import joblib

    X = np.array([[r[k] for k in FEATURES] for r in rows], dtype=float)
    y = np.array([r["label"] for r in rows])
    groups = np.array([r["scene"] for r in rows])
    make = lambda: HistGradientBoostingClassifier(  # noqa: E731
        max_iter=300, learning_rate=0.05, max_leaf_nodes=15, min_samples_leaf=12,
        l2_regularization=1.0, class_weight="balanced", random_state=0)
    folds = list(GroupKFold(n_splits=5).split(X, y, groups))
    oof = np.zeros(len(y))
    imp_acc = np.zeros(len(FEATURES))
    for tr, te in folds:
        m = make().fit(X[tr], y[tr])
        oof[te] = m.predict_proba(X[te])[:, 1]
        # Importance on the held-out fold only (on training data an overfit model ignores permutations).
        imp_acc += permutation_importance(m, X[te], y[te], n_repeats=5, random_state=0, scoring="roc_auc").importances_mean
    auc = roc_auc_score(y, oof)
    # Ablation: what each modality adds (same folds).
    groups_of = {
        "sar_only": [f for f in FEATURES if not f.startswith("s2") and f not in ("windMs", "currentMs", "waveHeightM")],
        "sar_metocean": [f for f in FEATURES if not f.startswith("s2")],
        "sar_metocean_s2": FEATURES,
    }
    ablation = {}
    for name, cols in groups_of.items():
        idx = [FEATURES.index(c) for c in cols]
        pred = np.zeros(len(y))
        for tr, te in folds:
            pred[te] = make().fit(X[tr][:, idx], y[tr]).predict_proba(X[te][:, idx])[:, 1]
        ablation[name] = {"rocAuc": round(roc_auc_score(y, pred), 4), "avgPrecision": round(average_precision_score(y, pred), 4)}
    ap = average_precision_score(y, oof)
    prec, rec, thr = precision_recall_curve(y, oof)
    # confirm: lowest threshold with >= 90 % precision; review: keep >= 95 % of true slicks.
    confirm = next((float(t) for p, t in zip(prec[:-1], thr) if p >= 0.90), 0.8)
    review = float(max((t for r, t in zip(rec[:-1], thr) if r >= 0.95), default=0.3))
    review = min(review, confirm - 0.05)
    at = lambda t: {  # noqa: E731
        "precision": float(((oof >= t) & (y == 1)).sum() / max((oof >= t).sum(), 1)),
        "recall": float(((oof >= t) & (y == 1)).sum() / max((y == 1).sum(), 1)),
    }
    model = make().fit(X, y)
    importance = sorted(zip(FEATURES, (imp_acc / len(folds)).tolist()), key=lambda kv: -kv[1])
    # Out-of-fold behaviour on the mined look-alike regimes (negatives only).
    kinds = np.array([r.get("kind", "slick") for r in rows])
    lookalikes = {}
    for k in ("lowwind", "algae", "background", "slick"):
        sel = (kinds == k) & (y == 0)
        if sel.any():
            lookalikes[k] = {"n": int(sel.sum()), "rejected": round(float((oof[sel] < review).mean()), 3),
                             "notConfirmed": round(float((oof[sel] < confirm).mean()), 3)}
    bundle = {
        "model": model, "features": FEATURES,
        "thresholds": {"confirm": round(confirm, 3), "review": round(review, 3)},
        "cv": {"rocAuc": round(auc, 4), "avgPrecision": round(ap, 4), "folds": 5, "grouping": "S1 scene",
               "atConfirm": at(confirm), "atReview": at(review)},
        "n_samples": {"total": int(len(y)), "oil": int(y.sum()), "lookalike": int((1 - y).sum())},
        "importance": [(k, round(v, 4)) for k, v in importance],
        "ablation": ablation,
        "lookalikeRejection": lookalikes,
        "labels": "positives: Cerulean-published slicks; negatives: U-Net candidates Cerulean did not publish",
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(bundle, out)
    return {k: v for k, v in bundle.items() if k != "model"}


def main() -> None:
    ap = argparse.ArgumentParser()
    base = Path(__file__).resolve().parents[1] / "data"
    ap.add_argument("--data", nargs="+", default=[str(base / "train"), str(base / "train_lookalikes")])
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--no-s2", action="store_true")
    ap.add_argument("--rows", default=str(Path(__file__).resolve().parents[1] / "data" / "verifier_rows.csv"))
    ap.add_argument("--reuse-rows", action="store_true", help="skip feature extraction, refit from CSV")
    ns = ap.parse_args()
    rows_path = Path(ns.rows)
    if ns.reuse_rows and rows_path.exists():
        with rows_path.open() as fh:
            rows = []
            for r in csv.DictReader(fh):
                row = {k: (float(r[k]) if r[k] not in ("", "nan") else np.nan) for k in FEATURES}
                row.update(label=int(r["label"]), scene=r["scene"], chip=r["chip"], kind=r.get("kind", "slick"))
                rows.append(row)
    else:
        seg = SlickSegmenter(UNET_WEIGHTS)
        chips = sorted(c for d in ns.data for c in Path(d).glob("*.npz"))
        rows = []
        with ThreadPoolExecutor(ns.workers) as pool:
            futs = {pool.submit(chip_rows, c, seg, not ns.no_s2): c for c in chips}
            for i, fut in enumerate(as_completed(futs), 1):
                try:
                    got = fut.result()
                except Exception as exc:
                    print(f"[verifier] {futs[fut].name} failed: {exc}", flush=True)
                    got = []
                rows.extend(got)
                if i % 20 == 0 or i == len(chips):
                    pos = sum(r["label"] for r in rows)
                    print(f"[verifier] {i}/{len(chips)} chips -> {len(rows)} candidates ({pos} oil)", flush=True)
        with rows_path.open("w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=FEATURES + ["label", "scene", "chip", "kind", "oilFrac"])
            w.writeheader()
            w.writerows(rows)
    summary = fit(rows, VERIFIER_MODEL)
    print("[verifier] " + json.dumps(summary, indent=1), flush=True)


if __name__ == "__main__":
    main()
