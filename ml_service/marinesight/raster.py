"""Georeferenced raster container on the EPSG:4326 PIX_DEG lattice."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable

import numpy as np
from rasterio import features as rio_features
from rasterio.transform import Affine, from_origin

from .config import MAX_TILE_PX, PIX_DEG


def snap_bbox(bbox: Iterable[float], pix: float = PIX_DEG) -> list[float]:
    """Expand a [west, south, east, north] bbox outward onto the pixel lattice."""
    w, s, e, n = bbox
    return [
        math.floor(w / pix) * pix,
        math.floor(s / pix) * pix,
        math.ceil(e / pix) * pix,
        math.ceil(n / pix) * pix,
    ]


def bbox_shape(bbox: Iterable[float], pix: float = PIX_DEG) -> tuple[int, int]:
    w, s, e, n = bbox
    return int(round((n - s) / pix)), int(round((e - w) / pix))


def split_tiles(bbox: Iterable[float], max_px: int = MAX_TILE_PX, pix: float = PIX_DEG) -> list[list[float]]:
    """Split a snapped bbox into lattice-aligned tiles no larger than max_px per side."""
    w, s, e, n = snap_bbox(bbox, pix)
    h_px, w_px = bbox_shape([w, s, e, n], pix)
    ny, nx = max(1, math.ceil(h_px / max_px)), max(1, math.ceil(w_px / max_px))
    ys = np.linspace(0, h_px, ny + 1).round().astype(int)
    xs = np.linspace(0, w_px, nx + 1).round().astype(int)
    tiles = []
    for j in range(ny):
        for i in range(nx):
            tiles.append([w + xs[i] * pix, n - ys[j + 1] * pix, w + xs[i + 1] * pix, n - ys[j] * pix])
    return tiles


@dataclass
class GeoRaster:
    data: np.ndarray  # (H, W) or (C, H, W)
    bbox: list[float]  # west, south, east, north

    @property
    def height(self) -> int:
        return self.data.shape[-2]

    @property
    def width(self) -> int:
        return self.data.shape[-1]

    @property
    def transform(self) -> Affine:
        w, s, e, n = self.bbox
        return from_origin(w, n, (e - w) / self.width, (n - s) / self.height)

    @property
    def pixel_size_m(self) -> tuple[float, float]:
        """(dy, dx) pixel size in metres at the raster's centre latitude."""
        w, s, e, n = self.bbox
        lat = math.radians((s + n) / 2)
        dy = (n - s) / self.height * 111_320.0
        dx = (e - w) / self.width * 111_320.0 * math.cos(lat)
        return dy, dx

    @property
    def pixel_area_km2(self) -> float:
        dy, dx = self.pixel_size_m
        return dy * dx / 1e6

    def pixel_to_lonlat(self, col: np.ndarray | float, row: np.ndarray | float):
        """Pixel corner coordinates (col, row) -> (lon, lat)."""
        w, s, e, n = self.bbox
        lon = w + np.asarray(col, dtype=float) * (e - w) / self.width
        lat = n - np.asarray(row, dtype=float) * (n - s) / self.height
        return lon, lat

    def lonlat_to_pixel(self, lon, lat):
        w, s, e, n = self.bbox
        col = (np.asarray(lon, dtype=float) - w) / (e - w) * self.width
        row = (n - np.asarray(lat, dtype=float)) / (n - s) * self.height
        return col, row

    def rasterize(self, geoms, all_touched: bool = False) -> np.ndarray:
        geoms = [g for g in geoms if g is not None and not g.is_empty]
        if not geoms:
            return np.zeros((self.height, self.width), dtype=np.uint8)
        return rio_features.rasterize(
            [(g, 1) for g in geoms],
            out_shape=(self.height, self.width),
            transform=self.transform,
            fill=0,
            all_touched=all_touched,
            dtype="uint8",
        )

    def crop(self, row0: int, col0: int, row1: int, col1: int) -> "GeoRaster":
        lon0, lat0 = self.pixel_to_lonlat(col0, row0)
        lon1, lat1 = self.pixel_to_lonlat(col1, row1)
        return GeoRaster(self.data[..., row0:row1, col0:col1], [float(lon0), float(lat1), float(lon1), float(lat0)])


def mosaic(tiles: list[GeoRaster], bbox: list[float], fill=0) -> GeoRaster:
    """Paste lattice-aligned tiles into one raster covering bbox."""
    bbox = snap_bbox(bbox)
    h, w = bbox_shape(bbox)
    first = tiles[0].data
    shape = (first.shape[0], h, w) if first.ndim == 3 else (h, w)
    out = np.full(shape, fill, dtype=first.dtype)
    target = GeoRaster(out, bbox)
    for t in tiles:
        c0, r0 = target.lonlat_to_pixel(t.bbox[0], t.bbox[3])
        r0, c0 = int(round(float(r0))), int(round(float(c0)))
        out[..., r0:r0 + t.height, c0:c0 + t.width] = t.data
    return target
