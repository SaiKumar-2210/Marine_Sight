"""Choose the U-Net inference mode (flip TTA, multi-scale fusion) and threshold on the
scene-held-out validation chips, and store the choice in the checkpoint.

    python -m training.eval_inference            # report only
    python -m training.eval_inference --write    # also update models/unet_s1_slick.pt
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import torch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from marinesight.config import UNET_WEIGHTS  # noqa: E402
from marinesight.unet import SlickSegmenter  # noqa: E402
from training.train_unet import load_chips, split_fixed  # noqa: E402

MODES = {
    "base": {"tta": False, "scales": [1.0], "fuse": "max"},
    "tta": {"tta": True, "scales": [1.0], "fuse": "max"},
    "multiscale_max": {"tta": False, "scales": [1.0, 0.5], "fuse": "max"},
    "multiscale_mean": {"tta": False, "scales": [1.0, 0.5], "fuse": "mean"},
    "tta_multiscale_max": {"tta": True, "scales": [1.0, 0.5], "fuse": "max"},
}
THRESHOLDS = [round(t, 2) for t in np.arange(0.3, 0.9, 0.05)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=str(Path(__file__).resolve().parents[1] / "data" / "train"))
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--weights", default=str(UNET_WEIGHTS))
    ap.add_argument("--min-gain", type=float, default=0.01, help="IoU gain over 'base' needed to adopt a costlier mode")
    ns = ap.parse_args()
    ap_path = Path(ns.weights)
    seg = SlickSegmenter(ap_path)
    val_names = set(json.loads((Path(__file__).resolve().parents[1] / "data" / "val_chips.json").read_text()))
    _, val = split_fixed(load_chips(Path(ns.data), seg.preprocess_version), val_names)
    results = {}
    for name, mode in MODES.items():
        stats = {t: [0, 0, 0] for t in THRESHOLDS}
        t0 = time.time()
        for c in val:
            prob = seg.predict(c["x"].astype(np.float32), **mode)
            m = c["y"] != 255
            gt = (c["y"] == 1) & m
            for t in THRESHOLDS:
                pr = (prob >= t) & m
                stats[t][0] += int((pr & gt).sum())
                stats[t][1] += int((pr & ~gt).sum())
                stats[t][2] += int((~pr & gt).sum())
        per_t = {t: {"iou": tp / max(tp + fp + fn, 1), "precision": tp / max(tp + fp, 1), "recall": tp / max(tp + fn, 1)}
                 for t, (tp, fp, fn) in stats.items()}
        best = max(per_t, key=lambda t: per_t[t]["iou"])
        results[name] = {"threshold": best, **{k: round(v, 4) for k, v in per_t[best].items()},
                         "secPerChip": round((time.time() - t0) / len(val), 2)}
        print(f"[infer] {name:20s} " + json.dumps(results[name]), flush=True)
    base = results["base"]["iou"]
    eligible = [n for n in results if n == "base" or results[n]["iou"] >= base + ns.min_gain]
    chosen = max(eligible, key=lambda n: results[n]["iou"])
    print(f"[infer] chosen: {chosen}", flush=True)
    # Hysteresis on top of the chosen mode: grow confident cores into weaker connected pixels.
    from marinesight.segment import binarize

    thr = results[chosen]["threshold"]
    probs = [seg.predict(c["x"].astype(np.float32), **MODES[chosen]) for c in val]
    hyst = {}
    for low in [None, 0.3, 0.4, 0.5, 0.6, 0.7]:
        if low is not None and low >= thr:
            continue
        tp = fp = fn = 0
        for c, prob in zip(val, probs):
            m = c["y"] != 255
            gt = (c["y"] == 1) & m
            pr = binarize(prob, thr, low) & m
            tp += int((pr & gt).sum()); fp += int((pr & ~gt).sum()); fn += int((~pr & gt).sum())
        hyst[str(low)] = {"iou": round(tp / max(tp + fp + fn, 1), 4), "precision": round(tp / max(tp + fp, 1), 4),
                          "recall": round(tp / max(tp + fn, 1), 4)}
        print(f"[infer] hysteresis low={low}: " + json.dumps(hyst[str(low)]), flush=True)
    best_low = max(hyst, key=lambda k: hyst[k]["iou"])
    use_low = None if best_low == "None" or hyst[best_low]["iou"] < hyst["None"]["iou"] + ns.min_gain else float(best_low)
    print(f"[infer] hysteresis chosen: {use_low}", flush=True)
    if ns.write:
        ckpt = torch.load(ap_path, map_location="cpu", weights_only=False)
        ckpt["inference"] = MODES[chosen]
        ckpt["threshold"] = results[chosen]["threshold"]
        ckpt["validation"] = {k: results[chosen][k] for k in ("iou", "precision", "recall")}
        ckpt["validation_modes"] = results
        ckpt["hysteresis_low"] = use_low
        ckpt["validation_hysteresis"] = hyst
        if use_low is not None:
            ckpt["validation"] = hyst[str(use_low)]
        torch.save(ckpt, ap_path)
        print(f"[infer] checkpoint updated: mode={chosen} threshold={results[chosen]['threshold']}", flush=True)


if __name__ == "__main__":
    main()
