"""Multi-modal false-positive filter: SAR morphology + MetOcean + Sentinel-2 optical -> P(oil).

A gradient-boosted classifier (trained by training/train_verifier.py on Cerulean-labelled
slicks vs. mined SAR look-alikes) weighs all modalities jointly. Missing modalities
(no S2 pass, cloud) are passed as NaN, which the model handles natively.
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np

from .config import VERIFIER_MODEL

FEATURES = [
    # SAR (Sentinel-1) — shape and radiometry of the candidate
    "logAreaKm2", "logLengthKm", "meanWidthKm", "polsbyPopper", "fillFactor", "nParts",
    "meanProb", "maxProb", "meanDb", "contrastDb", "sceneDarkFrac", "nearestSarVesselKm",
    # MetOcean — wind regime decides whether SAR can see oil at all
    "windMs", "currentMs", "waveHeightM",
    # Optical (Sentinel-2)
    "s2Available", "s2DtHours", "s2CloudFrac", "s2VisContrast", "s2NirContrast", "s2SwirContrast",
    "s2FaiDiff", "s2NdviDiff", "s2Glint",
]


def _nan(v):
    return np.nan if v is None else float(v)


def feature_vector(metrics: dict, scene_dark_frac: float, nearest_vessel_km, met: dict, optical: dict) -> dict:
    f = {
        "logAreaKm2": math.log10(max(metrics["areaKm2"], 1e-4)),
        "logLengthKm": math.log10(max(metrics.get("lengthKm") or 0.01, 0.01)),
        "meanWidthKm": _nan(metrics.get("meanWidthKm")),
        "polsbyPopper": _nan(metrics.get("polsbyPopper")),
        "fillFactor": _nan(metrics.get("fillFactor")),
        "nParts": float(metrics.get("nParts") or 1),
        "meanProb": _nan(metrics.get("meanProb")),
        "maxProb": _nan(metrics.get("maxProb")),
        "meanDb": _nan(metrics.get("meanDb")),
        "contrastDb": _nan(metrics.get("contrastDb")),
        "sceneDarkFrac": _nan(scene_dark_frac),
        "nearestSarVesselKm": _nan(nearest_vessel_km),
        "windMs": _nan(met.get("windMs")),
        "currentMs": _nan(met.get("currentMs")),
        "waveHeightM": _nan(met.get("waveHeightM")),
    }
    for k in FEATURES:
        if k.startswith("s2"):
            f[k] = _nan(optical.get(k))
    return f


class Verifier:
    def __init__(self, path: Path = VERIFIER_MODEL):
        import joblib

        bundle = joblib.load(path)
        self.model = bundle["model"]
        self.features = bundle["features"]
        self.thresholds = bundle.get("thresholds", {"confirm": 0.6, "review": 0.35})
        self.meta = {k: v for k, v in bundle.items() if k != "model"}

    def predict(self, feats: dict) -> float:
        x = np.array([[feats.get(k, np.nan) for k in self.features]], dtype=float)
        return float(self.model.predict_proba(x)[0, 1])


def indicators(feats: dict, optical_info: dict) -> list[str]:
    """Human-readable context shown next to the model's probability (not used for the decision)."""
    out = []
    w = feats.get("windMs")
    if w is not None and not np.isnan(w):
        if w < 2.5:
            out.append(f"Low wind {w:.1f} m/s: calm-sea look-alikes likely")
        elif w > 12:
            out.append(f"Strong wind {w:.1f} m/s: thin films mix down; visible slicks are usually thick")
        else:
            out.append(f"Wind {w:.1f} m/s inside the SAR oil-detection window (2.5–12 m/s)")
    if feats.get("s2Available") == 1:
        fai, ndvi = feats.get("s2FaiDiff"), feats.get("s2NdviDiff")
        if fai is not None and not np.isnan(fai) and fai > 0.01 and (ndvi or 0) > 0.05:
            out.append("Sentinel-2 shows a floating-algae spectral signature (FAI/NDVI raised)")
        elif optical_info.get("status") == "clear":
            out.append(f"Sentinel-2 clear view {abs(optical_info.get('dtHours', 0)):.1f} h from SAR pass")
        elif optical_info.get("status") in ("cloudy", "inconclusive"):
            out.append("Sentinel-2 pass cloudy / inconclusive over the slick")
    else:
        out.append("No Sentinel-2 pass within ±36 h")
    if feats.get("sceneDarkFrac", 0) > 0.3:
        out.append("Large part of the scene is dark (widespread low-wind areas)")
    return out
