"""Paths, environment loading and the pixel grid shared by every stage."""
from __future__ import annotations

import os
from pathlib import Path

ML_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = ML_ROOT.parent
DATA_DIR = ML_ROOT / "data"
MODELS_DIR = ML_ROOT / "models"
CACHE_DIR = Path(os.environ.get("MARINESIGHT_CACHE_DIR", ML_ROOT / "cache"))
QUICKLOOK_DIR = CACHE_DIR / "quicklooks"

UNET_WEIGHTS = MODELS_DIR / "unet_s1_slick.pt"
VERIFIER_MODEL = MODELS_DIR / "verifier_hgb.joblib"

# Pixel grid: 360 / 2**19 degrees (~73 m at the equator). This is the same lat/lon
# lattice SkyTruth Cerulean publishes its slick polygons on, so our masks align
# with theirs pixel-for-pixel when benchmarking.
PIX_DEG = 360.0 / 2 ** 19
MAX_TILE_PX = 2048  # Sentinel Hub Process API hard limit is 2500 px per side


def load_dotenv() -> None:
    env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


load_dotenv()
