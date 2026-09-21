"""Train the Sentinel-1 slick U-Net on chips produced by build_dataset.py.

    python -m training.train_unet --data ml_service/data/train --epochs 30

Split is by S1 scene (no scene appears in both train and validation). The decision
threshold is chosen on validation IoU and stored in the checkpoint with the metrics.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from marinesight.config import UNET_WEIGHTS  # noqa: E402
from marinesight.unet import SlickSegmenter, UNet, preprocess  # noqa: E402


def load_chips(data_dir: Path, version: int = 1):
    chips = []
    for npz in sorted(data_dir.glob("chip_*.npz")):
        meta = json.loads(npz.with_suffix(".json").read_text())
        d = np.load(npz)
        vv = d["vv_db"].astype(np.float32)
        valid = d["valid"]
        vv[~valid] = np.nan
        invalid = ~valid | d["land"]
        x = preprocess(vv, invalid, version).astype(np.float16)
        y = d["label"].copy()
        y[invalid & (y != 255)] = 0
        chips.append({"x": x, "y": y, "invalid": invalid, "meta": meta, "name": npz.stem, "oil_idx": np.argwhere(y == 1)})
    return chips


def split_fixed(chips, val_names: set):
    """Validation = the frozen chip list; training excludes every scene that appears in it."""
    val = [c for c in chips if c["name"] in val_names]
    val_scenes = {c["meta"]["scene"] for c in val}
    return [c for c in chips if c["meta"]["scene"] not in val_scenes], val


def split_by_scene(chips, val_frac: float, seed: int):
    scenes = sorted({c["meta"]["scene"] for c in chips})
    random.Random(seed).shuffle(scenes)
    val_scenes = set(scenes[: max(1, int(len(scenes) * val_frac))])
    return [c for c in chips if c["meta"]["scene"] not in val_scenes], [c for c in chips if c["meta"]["scene"] in val_scenes]


def sample_crop(chip, size: int, rng: random.Random):
    h, w = chip["y"].shape
    if len(chip["oil_idx"]) and rng.random() < 0.7:
        cy, cx = chip["oil_idx"][rng.randrange(len(chip["oil_idx"]))]
        y0 = int(np.clip(cy - rng.randint(size // 4, 3 * size // 4), 0, h - size))
        x0 = int(np.clip(cx - rng.randint(size // 4, 3 * size // 4), 0, w - size))
    else:
        y0, x0 = rng.randint(0, h - size), rng.randint(0, w - size)
    x = chip["x"][:, y0:y0 + size, x0:x0 + size].astype(np.float32)
    y = chip["y"][y0:y0 + size, x0:x0 + size]
    k = rng.randint(0, 3)
    x, y = np.rot90(x, k, axes=(1, 2)), np.rot90(y, k)
    if rng.random() < 0.5:
        x, y = x[:, :, ::-1], y[:, ::-1]
    x = x.copy()
    inval = x[2] > 0.5
    x[0] = np.where(inval, 0, np.clip(x[0] + rng.uniform(-0.05, 0.05), 0, 1))  # ±2 dB calibration jitter
    x[:2] += np.random.normal(0, 0.02, x[:2].shape).astype(np.float32) * (~inval)
    return x, y.copy()


def loss_fn(logits, target):
    valid = (target != 255).float()
    t = (target == 1).float()
    bce = F.binary_cross_entropy_with_logits(logits, t, weight=valid, pos_weight=torch.tensor(4.0))
    p = torch.sigmoid(logits) * valid
    inter = (p * t).sum()
    dice = 1 - (2 * inter + 1) / (p.sum() + (t * valid).sum() + 1)
    return bce + dice


def evaluate(model_path: Path, val, thresholds=np.arange(0.3, 0.91, 0.05)):
    seg = SlickSegmenter(model_path)
    stats = {round(float(t), 2): [0, 0, 0] for t in thresholds}  # tp, fp, fn
    for c in val:
        prob = seg.predict(c["x"].astype(np.float32))
        lab = c["y"]
        m = lab != 255
        gt = (lab == 1) & m
        for t in stats:
            pr = (prob >= t) & m
            stats[t][0] += int((pr & gt).sum())
            stats[t][1] += int((pr & ~gt).sum())
            stats[t][2] += int((~pr & gt).sum())
    res = {}
    for t, (tp, fp, fn) in stats.items():
        res[t] = {"iou": tp / max(tp + fp + fn, 1), "precision": tp / max(tp + fp, 1), "recall": tp / max(tp + fn, 1)}
    best = max(res, key=lambda t: res[t]["iou"])
    return best, res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=str(Path(__file__).resolve().parents[1] / "data" / "train"))
    ap.add_argument("--epochs", type=int, default=30)
    ap.add_argument("--crops-per-chip", type=int, default=4)
    ap.add_argument("--crop", type=int, default=256)
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--lr", type=float, default=2e-3)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", default=str(UNET_WEIGHTS))
    ap.add_argument("--preprocess", type=int, default=2)
    ap.add_argument("--val-chips", default=str(Path(__file__).resolve().parents[1] / "data" / "val_chips.json"))
    ap.add_argument("--init", default=None, help="warm-start from an existing checkpoint")
    ns = ap.parse_args()

    torch.manual_seed(ns.seed)
    np.random.seed(ns.seed)
    torch.set_num_threads(max(1, torch.get_num_threads()))
    rng = random.Random(ns.seed)
    chips = load_chips(Path(ns.data), ns.preprocess)
    if Path(ns.val_chips).exists():
        train, val = split_fixed(chips, set(json.loads(Path(ns.val_chips).read_text())))
    else:
        train, val = split_by_scene(chips, 0.15, ns.seed)
    print(f"[unet] chips={len(chips)} train={len(train)} val={len(val)} "
          f"oil_px_frac={np.mean([(c['y'] == 1).mean() for c in chips]):.4f}", flush=True)

    model = UNet()
    if ns.init:
        model.load_state_dict(torch.load(ns.init, map_location="cpu", weights_only=False)["state_dict"])
    opt = torch.optim.AdamW(model.parameters(), lr=ns.lr, weight_decay=1e-4)
    steps_per_epoch = max(1, len(train) * ns.crops_per_chip // ns.batch)
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=ns.lr, total_steps=ns.epochs * steps_per_epoch)
    out = Path(ns.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    arch = {"widths": [16, 32, 64, 128, 192]}
    for epoch in range(ns.epochs):
        model.train()
        t0, tot = time.time(), 0.0
        for _ in range(steps_per_epoch):
            xs, ys = zip(*(sample_crop(train[rng.randrange(len(train))], ns.crop, rng) for _ in range(ns.batch)))
            x = torch.from_numpy(np.stack(xs))
            y = torch.from_numpy(np.stack(ys).astype(np.int64))[:, None]
            loss = loss_fn(model(x), y)
            opt.zero_grad()
            loss.backward()
            opt.step()
            sched.step()
            tot += loss.item()
        print(f"[unet] epoch {epoch + 1}/{ns.epochs} loss={tot / steps_per_epoch:.4f} ({time.time() - t0:.0f}s)", flush=True)
        torch.save({"state_dict": model.state_dict(), "arch": arch, "threshold": 0.5, "preprocess": ns.preprocess}, out)

    best, res = evaluate(out, val)
    print(f"[unet] validation (scene-held-out) best threshold={best} " + json.dumps(res[best]), flush=True)
    torch.save({
        "state_dict": model.state_dict(), "arch": arch, "threshold": best, "preprocess": ns.preprocess,
        "validation": res[best], "n_train_chips": len(train), "n_val_chips": len(val),
        "labels": "SkyTruth Cerulean slick polygons (human-reviewed + high-confidence), Sentinel-1 VV",
        "pixel_deg": 360 / 2 ** 19,
    }, out)
    print(f"[unet] saved {out}", flush=True)


if __name__ == "__main__":
    main()
