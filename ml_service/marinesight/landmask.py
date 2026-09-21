"""Natural Earth 1:10m land + minor islands mask with a coastal buffer."""
from __future__ import annotations

from functools import lru_cache

import numpy as np
from scipy import ndimage
from shapely import STRtree, box
from shapely.geometry.base import BaseGeometry

from .config import DATA_DIR
from .raster import GeoRaster

SOURCES = ["ne_10m_land.zip", "ne_10m_minor_islands.zip"]


@lru_cache(maxsize=1)
def _land_index() -> tuple[STRtree, list[BaseGeometry]]:
    import pyogrio

    geoms: list[BaseGeometry] = []
    for name in SOURCES:
        path = DATA_DIR / name
        if not path.exists():
            continue
        df = pyogrio.read_dataframe(f"zip://{path}")
        for g in df.geometry:
            if g is None:
                continue
            geoms.extend(list(g.geoms) if hasattr(g, "geoms") else [g])
    return STRtree(geoms), geoms


def land_geoms(bbox: list[float]) -> list[BaseGeometry]:
    tree, geoms = _land_index()
    q = box(*bbox)
    return [geoms[i] for i in tree.query(q, predicate="intersects")]


def land_mask(raster: GeoRaster, buffer_px: int = 3) -> np.ndarray:
    """True over land (plus a small coastal buffer to suppress shoreline wind-shadow)."""
    geoms = land_geoms(raster.bbox)
    mask = raster.rasterize(geoms, all_touched=True).astype(bool)
    if buffer_px and mask.any():
        mask = ndimage.binary_dilation(mask, iterations=buffer_px)
    return mask
