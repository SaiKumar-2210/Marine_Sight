"""CFAR bright-target detection: vessels visible in the same Sentinel-1 pass as the slick.

A ship at the moment of acquisition is a strong point scatterer on dark sea. These
targets need no AIS, so they also reveal 'dark' vessels that switched transponders off.
Two safeguards keep islands, rocks and platforms from being reported as ships:
  * spatial: detections are clustered; clusters wider than a large ship are static structures,
  * temporal (`static_targets`): anything bright at the same spot on earlier passes is static.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
from scipy import ndimage

from .raster import GeoRaster

KM_PER_DEG = 111.32

# Prior-pass search limits (see `_prior_passes`): a bounded window, then one archive probe.
LOOKBACK_DAYS = 5
ARCHIVE_PROBE_DAYS = 30  # single bounded probe when the 5-day window is empty
CELL_DEG = 0.05  # ~5 km: targets in one cell share a chip, so neighbours cost one fetch


def _cfar(db: np.ndarray, valid: np.ndarray, k_sigma: float, window: int = 41) -> np.ndarray:
    lin = np.where(valid, 10 ** (np.nan_to_num(db, nan=-40.0) / 10), 0.0).astype(np.float32)
    w = valid.astype(np.float32)
    n = ndimage.uniform_filter(w, window)
    mu = ndimage.uniform_filter(lin, window) / np.maximum(n, 1e-3)
    mu2 = ndimage.uniform_filter(lin * lin, window) / np.maximum(n, 1e-3)
    sd = np.sqrt(np.maximum(mu2 - mu * mu, 1e-12))
    return valid & (lin > mu + k_sigma * sd)


def detect_sar_vessels(vv_db: GeoRaster, invalid: np.ndarray, k_sigma: float = 6.0, min_peak_db: float = 0.0,
                       cluster_m: float = 500.0, max_vessel_extent_m: float = 600.0) -> list[dict]:
    db = vv_db.data
    valid = ~invalid & np.isfinite(db)
    hits = _cfar(db, valid, k_sigma) & (db > min_peak_db)
    # Keep well away from land/no-data edges where clutter statistics are unreliable.
    hits &= ndimage.binary_erosion(valid, iterations=4)
    dy, dx = vv_db.pixel_size_m
    px_m = (dy + dx) / 2
    # Cluster detections within `cluster_m`: one ship can split into several bright pixels,
    # and an island's rocky shore shows up as a scatter of bright points.
    grow = max(1, int(round(cluster_m / 2 / px_m)))
    clusters, count = ndimage.label(ndimage.binary_dilation(hits, iterations=grow))
    out = []
    for i, sl in enumerate(ndimage.find_objects(clusters), start=1):
        if sl is None:
            continue
        comp = hits[sl] & (clusters[sl] == i)
        rows, cols = np.nonzero(comp)
        if len(rows) == 0:
            continue
        r = rows + sl[0].start
        c = cols + sl[1].start
        extent_m = float(max(np.ptp(r) + 1, np.ptp(c) + 1) * px_m)
        vals = db[r, c]
        k = int(np.argmax(vals))
        lon, lat = vv_db.pixel_to_lonlat(c[k] + 0.5, r[k] + 0.5)
        out.append({
            "lon": round(float(lon), 6), "lat": round(float(lat), 6),
            "peakDb": round(float(vals[k]), 2), "pixels": int(len(r)), "extentM": round(extent_m, 1),
            "static": extent_m > max_vessel_extent_m or None,  # None = not yet checked over time
            "staticReason": "extended bright cluster (island / structure)" if extent_m > max_vessel_extent_m else None,
        })
    out.sort(key=lambda v: v["peakDb"], reverse=True)
    return out[:2000]


def _prior_passes(bbox: list[float], acquired: datetime, max_scenes: int) -> list[str]:
    """Earlier Sentinel-1 passes over `bbox`, newest first, with a hard cap on how far back we look.

    Each pass costs a Copernicus fetch, so the search is bounded:
      * only the last LOOKBACK_DAYS days are searched, then
      * if that window holds no pass at all, a single extra query takes just the most recent scene
        before it — one scene, not a walk back through the archive.
    """
    from .cdse import CdseError, search

    def distinct(features: list[dict]) -> list[str]:
        seen, out = set(), []
        # Sort here rather than trusting the caller's order: the newest passes are the ones worth
        # spending a fetch on.
        for f in sorted(features, key=lambda f: f["properties"]["datetime"], reverse=True):
            key = f["properties"]["datetime"][:16]
            if key in seen:
                continue
            seen.add(key)
            out.append(f["properties"]["datetime"])
            if len(out) >= max_scenes:
                break
        return out

    end = acquired - timedelta(hours=6)
    try:
        prior = distinct(search("sentinel-1-grd", bbox, acquired - timedelta(days=LOOKBACK_DAYS), end))
        if prior:
            return prior
        # Nothing within the capped window: one bounded probe, and take only its most recent scan.
        older = search("sentinel-1-grd", bbox, acquired - timedelta(days=ARCHIVE_PROBE_DAYS),
                       acquired - timedelta(days=LOOKBACK_DAYS))
        return distinct(older)[:1]
    except CdseError:
        return []


def static_targets(targets: list[dict], acquired: datetime, max_scenes: int = 2, radius_m: float = 200.0,
                   max_fetches: int = 12) -> None:
    """Mark targets that are also bright on earlier passes as static (in place).

    Targets are grouped into shared chips (CELL_DEG cells) so neighbouring detections cost one
    Copernicus fetch between them instead of one each, and the whole call is capped at
    `max_fetches` chips. Anything left unchecked keeps static=False and says so, rather than
    holding the scan up for minutes.
    """
    from .cdse import CdseError, fetch_s1_vv_db

    pending = [t for t in targets if t.get("static") is None]
    if not pending:
        return
    groups: dict[tuple[int, int], list[dict]] = {}
    for t in pending:
        groups.setdefault((int(t["lat"] // CELL_DEG), int(t["lon"] // CELL_DEG)), []).append(t)

    fetches = 0
    probes = 0  # catalog searches are cheap but not free; bound them alongside the fetches
    for members in groups.values():
        for t in members:
            t["priorPassesChecked"] = 0
            t["priorPassesBright"] = 0
            t["static"] = False
        half = 0.012
        bbox = [min(t["lon"] for t in members) - half, min(t["lat"] for t in members) - half,
                max(t["lon"] for t in members) + half, max(t["lat"] for t in members) + half]
        if fetches >= max_fetches or probes >= 2 * max_fetches:
            for t in members:
                t["staticCheck"] = "skipped (prior-pass budget spent)"
            continue
        probes += 1
        prior = _prior_passes(bbox, acquired, max_scenes)
        if not prior:
            for t in members:
                t["staticCheck"] = f"no earlier Sentinel-1 pass within {LOOKBACK_DAYS} days"
            continue
        hits = {id(t): 0 for t in members}
        checked = 0
        for when in prior:
            if fetches >= max_fetches:
                break
            fetches += 1  # count the attempt: a failing fetch costs time too
            try:
                chip = fetch_s1_vv_db(bbox, when)
            except CdseError:
                continue
            valid = np.isfinite(chip.data)
            if valid.mean() < 0.5:
                continue
            checked += 1
            bright_all = _cfar(chip.data, valid, 6.0, window=21)
            r_px = max(1, int(round(radius_m / ((chip.pixel_size_m[0] + chip.pixel_size_m[1]) / 2))))
            for t in members:
                col, row = chip.lonlat_to_pixel(t["lon"], t["lat"])
                r0, c0 = int(row), int(col)
                sl = (slice(max(0, r0 - r_px), r0 + r_px + 1), slice(max(0, c0 - r_px), c0 + r_px + 1))
                if np.any(bright_all[sl] & (chip.data[sl] > 0.0)):
                    hits[id(t)] += 1
        for t in members:
            t["priorPassesChecked"] = checked
            t["priorPassesBright"] = hits[id(t)]
            # Speckle can hide a rock on one pass; ships almost never sit on the same 200 m spot on other days.
            t["static"] = bool(checked) and hits[id(t)] / checked >= 0.5
            if t["static"]:
                t["staticReason"] = f"bright at the same spot on {hits[id(t)]}/{checked} earlier Sentinel-1 passes"
            elif not checked:
                t["staticCheck"] = "earlier passes unusable (no valid pixels)"
