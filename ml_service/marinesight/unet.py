"""U-Net for Sentinel-1 oil-slick semantic segmentation (Cerulean-style), with shared preprocessing."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from scipy import ndimage

IN_CHANNELS = 3
BG_SIGMA_PX = 40  # ~3 km local-background scale at 73 m pixels


def _normconv(values: np.ndarray, weights: np.ndarray, sigma: float, down: int = 1):
    """Weighted Gaussian mean (normalised convolution); `down` evaluates it on a coarser grid."""
    if down > 1:
        h, w = values.shape
        hp, wp = -(-h // down) * down, -(-w // down) * down
        v = np.zeros((hp, wp), np.float32)
        wt = np.zeros((hp, wp), np.float32)
        v[:h, :w] = values * weights
        wt[:h, :w] = weights
        v = v.reshape(hp // down, down, wp // down, down).sum((1, 3))
        wt = wt.reshape(hp // down, down, wp // down, down).sum((1, 3))
        num = ndimage.gaussian_filter(v, sigma / down, mode="nearest")
        den = ndimage.gaussian_filter(wt, sigma / down, mode="nearest")
        num = np.repeat(np.repeat(num, down, 0), down, 1)[:h, :w]
        den = np.repeat(np.repeat(den, down, 0), down, 1)[:h, :w] / (down * down)
        return num / (down * down), den
    num = ndimage.gaussian_filter(values * weights, sigma, mode="nearest")
    den = ndimage.gaussian_filter(weights, sigma, mode="nearest")
    return num, den


def preprocess(vv_db: np.ndarray, invalid: np.ndarray, version: int = 1) -> np.ndarray:
    """VV dB (NaN = no data) + invalid mask (no data or land) -> (3, H, W) float32 model input.

    ch0: absolute backscatter, (dB + 35) / 40
    ch1: anomaly vs. the local sea-clutter background (normalised convolution, so land/no-data
         don't bias it). Version 2 estimates that background from non-dark pixels only, falling
         back to a ~12 km scale where few are left, so a wide slick doesn't darken its own reference.
    ch2: invalid mask
    """
    invalid = invalid | ~np.isfinite(vv_db)
    valid = (~invalid).astype(np.float32)
    db = np.where(invalid, 0.0, vv_db).astype(np.float32)
    num, den = _normconv(db, valid, BG_SIGMA_PX)
    background = num / np.maximum(den, 1e-3)
    if version >= 2:
        sea = (valid > 0) & (db > background - 1.5)
        sea_w = sea.astype(np.float32)
        n2, d2 = _normconv(db, sea_w, BG_SIGMA_PX)
        nl, dl = _normconv(db, sea_w, 4 * BG_SIGMA_PX, down=4)
        local = n2 / np.maximum(d2, 1e-3)
        large = nl / np.maximum(dl, 1e-3)
        wgt = np.clip(d2 / 0.3, 0.0, 1.0)
        background = np.where(dl > 1e-3, wgt * local + (1 - wgt) * large, background)
    ch0 = np.clip((db + 35.0) / 40.0, 0.0, 1.0)
    ch1 = np.clip((db - background) / 6.0, -2.0, 2.0)
    ch0[invalid] = 0.0
    ch1[invalid] = 0.0
    return np.stack([ch0, ch1, invalid.astype(np.float32)]).astype(np.float32)


class _Block(nn.Module):
    def __init__(self, cin: int, cout: int):
        super().__init__()
        self.net = nn.Sequential(
            nn.Conv2d(cin, cout, 3, padding=1, bias=False), nn.BatchNorm2d(cout), nn.ReLU(inplace=True),
            nn.Conv2d(cout, cout, 3, padding=1, bias=False), nn.BatchNorm2d(cout), nn.ReLU(inplace=True),
        )

    def forward(self, x):
        return self.net(x)


class UNet(nn.Module):
    def __init__(self, in_ch: int = IN_CHANNELS, widths=(16, 32, 64, 128, 192)):
        super().__init__()
        self.downs = nn.ModuleList()
        c = in_ch
        for w in widths:
            self.downs.append(_Block(c, w))
            c = w
        self.ups = nn.ModuleList()
        self.upconvs = nn.ModuleList()
        for w in reversed(widths[:-1]):
            self.upconvs.append(nn.ConvTranspose2d(c, w, 2, stride=2))
            self.ups.append(_Block(w * 2, w))
            c = w
        self.head = nn.Conv2d(c, 1, 1)

    def forward(self, x):
        skips = []
        for i, block in enumerate(self.downs):
            x = block(x)
            if i < len(self.downs) - 1:
                skips.append(x)
                x = F.max_pool2d(x, 2)
        for up, block, skip in zip(self.upconvs, self.ups, reversed(skips)):
            x = up(x)
            x = block(torch.cat([x, skip], dim=1))
        return self.head(x)


class SlickSegmenter:
    """Loads trained weights and runs overlapped sliding-window inference over a scene raster."""

    def __init__(self, weights: Path, device: str = "cpu"):
        ckpt = torch.load(weights, map_location=device, weights_only=False)
        self.model = UNet(**ckpt.get("arch", {}))
        self.model.load_state_dict(ckpt["state_dict"])
        self.model.eval()
        self.device = device
        self.threshold = float(ckpt.get("threshold", 0.5))
        # Inference mode selected on the scene-held-out validation set (training/eval_inference.py).
        self.inference = dict(ckpt.get("inference", {"tta": False, "scales": [1.0], "fuse": "max"}))
        self.preprocess_version = int(ckpt.get("preprocess", 1))
        self.low_threshold = ckpt.get("hysteresis_low")  # None = plain threshold
        self.meta = {k: v for k, v in ckpt.items() if k not in ("state_dict",)}

    def prep(self, vv_db: np.ndarray, invalid: np.ndarray) -> np.ndarray:
        """Preprocess exactly as this checkpoint was trained."""
        return preprocess(vv_db, invalid, self.preprocess_version)

    def predict(self, x: np.ndarray, tta=None, scales=None, fuse=None, **kw) -> np.ndarray:
        """x: (C, H, W) preprocessed input -> (H, W) oil probability, using the configured inference mode."""
        import cv2

        tta = self.inference.get("tta", False) if tta is None else tta
        scales = self.inference.get("scales", [1.0]) if scales is None else scales
        fuse = self.inference.get("fuse", "max") if fuse is None else fuse
        _, h, w = x.shape
        outs = []
        for s in scales:
            if s == 1.0:
                outs.append(self._predict(x, tta=tta, **kw))
                continue
            size = (max(8, int(round(w * s))), max(8, int(round(h * s))))
            xs = np.stack([cv2.resize(c, size, interpolation=cv2.INTER_AREA) for c in x])
            xs[2] = (xs[2] > 0.5).astype(np.float32)
            p = self._predict(xs, tta=tta, **kw)
            outs.append(cv2.resize(p, (w, h), interpolation=cv2.INTER_LINEAR))
        out = np.maximum.reduce(outs) if fuse == "max" else np.mean(outs, axis=0)
        out[x[2] > 0.5] = 0.0
        return out

    def _forward(self, inp: torch.Tensor, tta: bool) -> np.ndarray:
        if not tta:
            return torch.sigmoid(self.model(inp)).cpu().numpy()[:, 0]
        acc = torch.zeros(inp.shape[0], 1, *inp.shape[2:])
        for dims in ([], [3], [2], [2, 3]):
            t = torch.flip(inp, dims) if dims else inp
            y = torch.sigmoid(self.model(t))
            acc += torch.flip(y, dims) if dims else y
        return (acc / 4).cpu().numpy()[:, 0]

    @torch.no_grad()
    def _predict(self, x: np.ndarray, window: int = 512, overlap: int = 96, batch: int = 4,
                 progress: Optional[callable] = None, tta: bool = False) -> np.ndarray:
        """Sliding-window inference at the input's own resolution."""
        _, h, w = x.shape
        ph, pw = max(h, window), max(w, window)
        if (ph, pw) != (h, w):
            pad = np.zeros((x.shape[0], ph, pw), dtype=x.dtype)
            pad[2] = 1.0
            pad[:, :h, :w] = x
            x = pad
        step = window - overlap
        ys = list(range(0, ph - window + 1, step))
        xs = list(range(0, pw - window + 1, step))
        if ys[-1] != ph - window:
            ys.append(ph - window)
        if xs[-1] != pw - window:
            xs.append(pw - window)
        ramp = np.minimum(np.arange(window) + 1, np.arange(window)[::-1] + 1).astype(np.float32)
        wgt = np.minimum(np.minimum.outer(ramp, ramp), overlap // 2 + 1)
        acc = np.zeros((ph, pw), dtype=np.float32)
        norm = np.zeros((ph, pw), dtype=np.float32)
        coords = [(y, xx) for y in ys for xx in xs]
        for i in range(0, len(coords), batch):
            chunk = coords[i:i + batch]
            tiles = [x[:, y:y + window, xx:xx + window] for y, xx in chunk]
            # Skip windows that are entirely land / no data.
            live = [j for j, t in enumerate(tiles) if t[2].mean() < 0.999]
            if live:
                inp = torch.from_numpy(np.stack([tiles[j] for j in live])).to(self.device)
                prob = self._forward(inp, tta)
                for k, j in enumerate(live):
                    y, xx = chunk[j]
                    acc[y:y + window, xx:xx + window] += prob[k] * wgt
                    norm[y:y + window, xx:xx + window] += wgt
            if progress:
                progress(min(i + batch, len(coords)), len(coords))
        out = np.where(norm > 0, acc / np.maximum(norm, 1e-6), 0.0)[:h, :w]
        out[x[2, :h, :w] > 0.5] = 0.0
        return out
