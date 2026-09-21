"""Vessel attribution: which ship most plausibly released this slick?

Evidence combined for every candidate source:

1. AIS track back-propagation. Every historical AIS position p(t) of a vessel is treated as a
   hypothetical discharge point. Oil released there at time t has, by the SAR acquisition time T,
   drifted to q(t) = p(t) + (current + 3 % wind) · (T - t). If the vessel caused the slick, its
   drift-corrected track q(t) lies along the slick. We score:
     - coverage: share of the slick's pixels explained by q(t) (within a tolerance that grows with
       oil age, because drift estimates degrade over time),
     - offset: chamfer distance between the explained slick pixels and q(t),
     - ordering: the youngest oil must sit at the slick head (the narrow, dark, fresh end),
     - vessel-type prior (tankers > cargo > others) and discharge-plausible speed.
2. Coincident SAR targets: a ship visible in the same Sentinel-1 pass at the slick head is the
   strongest single cue (Cerulean's "coincident vessel" class). Targets are cross-matched to AIS
   positions interpolated at T; unmatched ones are flagged as AIS-dark vessels. A ship that stopped
   discharging and kept going sits ahead of the head on the slick's axis ("recent vessel").
   Bright targets that persist across earlier passes are islands/rocks/platforms, not ships.
3. Offshore infrastructure within a short distance of either slick end.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Optional

import numpy as np

from .metocean import drift_velocity

KM_PER_DEG = 111.32
TYPE_PRIOR = {"tanker": 1.0, "cargo": 0.8, "passenger": 0.55, "fishing": 0.55, "tug": 0.5, "other": 0.6}

# Offshore production assets near monitored areas (lon, lat).
PLATFORMS = [
    {"id": "MUMBAI_HIGH", "name": "Mumbai High field", "lon": 71.33, "lat": 19.42},
    {"id": "MUMBAI_HIGH_S", "name": "Mumbai High South", "lon": 71.42, "lat": 19.18},
    {"id": "UPPER_ZAKUM", "name": "Upper Zakum field", "lon": 53.65, "lat": 24.85},
    {"id": "FORTIES", "name": "Forties field", "lon": 0.90, "lat": 57.73},
    {"id": "EKOFISK", "name": "Ekofisk complex", "lon": 3.22, "lat": 56.55},
    {"id": "GOM_MARS", "name": "Mars TLP (Mississippi Canyon)", "lon": -89.22, "lat": 28.17},
    {"id": "GOM_TAYLOR", "name": "Taylor Energy MC-20 seep site", "lon": -88.97, "lat": 28.94},
    {"id": "BONGA", "name": "Bonga FPSO", "lon": 4.60, "lat": 4.55},
]


def ship_category(ais_type) -> str:
    try:
        code = int(ais_type)
    except (TypeError, ValueError):
        s = str(ais_type or "").upper()
        for key, cat in (("TANK", "tanker"), ("CARGO", "cargo"), ("CONTAIN", "cargo"), ("PASS", "passenger"),
                         ("FISH", "fishing"), ("TUG", "tug")):
            if key in s:
                return cat
        return "other"
    if 80 <= code <= 89:
        return "tanker"
    if 70 <= code <= 79:
        return "cargo"
    if 60 <= code <= 69:
        return "passenger"
    if code == 30:
        return "fishing"
    if code in (31, 32, 52):
        return "tug"
    return "other"


class LocalFrame:
    """Equirectangular km frame around a reference point (accurate to <0.5 % over ~200 km)."""

    def __init__(self, lon0: float, lat0: float):
        self.lon0, self.lat0 = lon0, lat0
        self.kx = KM_PER_DEG * math.cos(math.radians(lat0))

    def xy(self, lon, lat) -> np.ndarray:
        return np.column_stack([(np.asarray(lon) - self.lon0) * self.kx, (np.asarray(lat) - self.lat0) * KM_PER_DEG])

    def lonlat(self, xy: np.ndarray) -> np.ndarray:
        return np.column_stack([xy[:, 0] / self.kx + self.lon0, xy[:, 1] / KM_PER_DEG + self.lat0])


def _interp_track(points: list[dict], t0: datetime, t1: datetime, step_s: int = 120, max_gap_h: float = 3.0):
    """Resample an AIS track (sorted dicts with ts/lon/lat) to step_s, never bridging gaps > max_gap_h."""
    ts = np.array([p["ts"].timestamp() for p in points])
    lon = np.array([p["lon"] for p in points])
    lat = np.array([p["lat"] for p in points])
    order = np.argsort(ts)
    ts, lon, lat = ts[order], lon[order], lat[order]
    grid = np.arange(max(ts[0], t0.timestamp()), min(ts[-1], t1.timestamp()) + 1, step_s)
    if len(ts) == 1 or len(grid) == 0:
        return ts, lon, lat
    idx = np.clip(np.searchsorted(ts, grid), 1, len(ts) - 1)
    gap = ts[idx] - ts[idx - 1]
    ok = gap <= max_gap_h * 3600
    # Keep original fixes inside gaps as single points.
    g = grid[ok]
    return (np.concatenate([g, ts]), np.concatenate([np.interp(g, ts, lon), lon]),
            np.concatenate([np.interp(g, ts, lat), lat]))


def _position_at(points: list[dict], when: datetime, max_gap_h: float = 1.0) -> Optional[tuple[float, float]]:
    t = when.timestamp()
    ts = np.array([p["ts"].timestamp() for p in points])
    order = np.argsort(ts)
    ts = ts[order]
    if t < ts[0] - 600 or t > ts[-1] + 600:
        return None
    lon = np.array([p["lon"] for p in points])[order]
    lat = np.array([p["lat"] for p in points])[order]
    i = int(np.clip(np.searchsorted(ts, t), 1, len(ts) - 1)) if len(ts) > 1 else 0
    if len(ts) == 1:
        return float(lon[0]), float(lat[0])
    if ts[i] - ts[i - 1] > max_gap_h * 3600 and min(abs(t - ts[i]), abs(t - ts[i - 1])) > 600:
        return None
    return float(np.interp(t, ts, lon)), float(np.interp(t, ts, lat))


def _ais_match(tracks: list[dict], when: datetime, frame: "LocalFrame", txy: np.ndarray, max_km: float = 1.5):
    """AIS vessel whose interpolated position at `when` is within max_km of a SAR target."""
    match = None
    for tr in tracks:
        pos = _position_at(tr["points"], when)
        if pos is None:
            continue
        dm = float(np.linalg.norm(frame.xy([pos[0]], [pos[1]])[0] - txy))
        if dm < max_km and (match is None or dm < match[1]):
            match = (tr, dm)
    return match


def slick_head(metrics: dict, drift_uv: tuple[float, float], sar_targets: list[dict], frame: LocalFrame) -> dict:
    """Pick the source end of the slick: narrow + dark + upwind, or a ship sitting on it."""
    ends = metrics.get("endpoints")
    profs = metrics.get("endProfiles")
    if not ends or not profs:
        c = metrics["centroid"]
        return {"lon": c[0], "lat": c[1], "confidence": 0.0, "tail": {"lon": c[0], "lat": c[1]}, "evidence": []}
    exy = frame.xy([ends[0][0], ends[1][0]], [ends[0][1], ends[1][1]])
    w = [p["widthKm"] or 0.0 for p in profs]
    d = [p["meanDb"] if p.get("meanDb") is not None else 0.0 for p in profs]
    evidence = []
    z = np.zeros(2)
    wmax = max(max(w), 1e-6)
    # Darkness is only comparable when both ends have interior (non-mixed) pixels; capped so it can't
    # override width and drift on its own.
    dark_ok = all(p.get("interiorPixels", 20) >= 20 and p["meanDb"] is not None for p in profs)
    for k in (0, 1):
        o = 1 - k
        z[k] += 1.2 * (w[o] - w[k]) / wmax  # narrower end
        if dark_ok:
            z[k] += float(np.clip(0.2 * (d[o] - d[k]), -0.8, 0.8))  # darker end (dB)
    u, v = drift_uv
    sp = math.hypot(u, v)
    if sp > 0.02:
        axis = exy[1] - exy[0]
        axis /= max(np.linalg.norm(axis), 1e-6)
        along = (axis[0] * u + axis[1] * v) / sp  # >0: drift carries oil from end0 towards end1
        z[0] += 0.6 * along
        z[1] -= 0.6 * along
    for k in (0, 1):
        for t in sar_targets:
            dk = float(np.linalg.norm(frame.xy([t["lon"]], [t["lat"]])[0] - exy[k]))
            if dk < 2.0:
                z[k] += 2.5 * (1 - dk / 2.0)
                evidence.append(f"SAR vessel {dk:.1f} km from end {k}")
    k = int(np.argmax(z))
    conf = float(1 / (1 + math.exp(-2.0 * (z[k] - z[1 - k]))))
    return {
        "lon": ends[k][0], "lat": ends[k][1], "confidence": round(2 * conf - 1, 3),
        "tail": {"lon": ends[1 - k][0], "lat": ends[1 - k][1]},
        "endScores": [round(float(x), 3) for x in z], "evidence": evidence,
    }


def _score_track(track: dict, T: datetime, drift: tuple[float, float], frame: LocalFrame,
                 slick_xy: np.ndarray, head_xy: np.ndarray, tail_xy: np.ndarray, head_conf: float,
                 lookback_h: float) -> Optional[dict]:
    t0 = T - timedelta(hours=lookback_h)
    ts, lon, lat = _interp_track(track["points"], t0, T + timedelta(minutes=10))
    sel = (ts >= t0.timestamp()) & (ts <= T.timestamp() + 600)
    if sel.sum() == 0:
        return None
    ts, lon, lat = ts[sel], lon[sel], lat[sel]
    age_s = np.clip(T.timestamp() - ts, 0, None)
    p = frame.xy(lon, lat)
    q = p + np.column_stack([drift[0] * age_s, drift[1] * age_s]) / 1000.0  # km
    tol = 1.0 + 0.25 * math.hypot(*drift) * age_s / 1000.0 + 0.02 * age_s / 3600.0
    # Nearest drift-corrected release point for every slick pixel sample.
    d = np.linalg.norm(slick_xy[:, None, :] - q[None, :, :], axis=-1)
    j = np.argmin(d, axis=1)
    dmin = d[np.arange(len(slick_xy)), j]
    explained = dmin <= tol[j]
    coverage = float(explained.mean())
    if coverage < 0.05:
        return {"mmsi": track["mmsi"], "coverage": coverage, "score": 0.0}
    chamfer = float(dmin[explained].mean())
    # Ordering: youngest release should map to the head end, oldest towards the tail.
    axis = tail_xy - head_xy
    L = max(np.linalg.norm(axis), 1e-6)
    pos = ((slick_xy[explained] - head_xy) @ axis) / (L * L)  # 0 at head, 1 at tail
    age_matched = age_s[j[explained]]
    order_corr = 0.0
    if explained.sum() > 10 and np.std(pos) > 1e-3 and np.std(age_matched) > 1:
        order_corr = float(np.corrcoef(pos, age_matched)[0, 1])  # +1: older towards the tail
    order_factor = 1.0 + 0.4 * head_conf * order_corr
    # Where was the vessel when the image was taken, relative to the head?
    pos_T = _position_at(track["points"], T)
    dist_head_T = None
    if pos_T is not None:
        dist_head_T = float(np.linalg.norm(frame.xy([pos_T[0]], [pos_T[1]])[0] - head_xy))
    release = age_s[j[explained]]
    t_first = T - timedelta(seconds=float(release.max()))
    t_last = T - timedelta(seconds=float(release.min()))
    sog = [pt.get("sog") for pt in track["points"] if pt.get("sog") is not None]
    speed_ok = 1.0
    if sog and np.median(sog) < 0.5:
        speed_ok = 0.85  # anchored ships do leak, but discharge while under way is far more common
    prior = TYPE_PRIOR.get(ship_category(track.get("shipType")), 0.6)
    score = (coverage ** 0.7) * math.exp(-chamfer / 2.0) * order_factor * prior * speed_ok
    step = max(1, len(q) // 150)
    return {
        "mmsi": track["mmsi"],
        "coverage": round(coverage, 3),
        "meanOffsetKm": round(chamfer, 3),
        "orderCorrelation": round(order_corr, 3),
        "typePrior": prior,
        "score": round(float(score), 4),
        "releaseWindow": [t_first.isoformat(), t_last.isoformat()],
        "distanceToHeadAtAcquisitionKm": None if dist_head_T is None else round(dist_head_T, 2),
        "positionAtAcquisition": None if pos_T is None else {"lon": pos_T[0], "lat": pos_T[1]},
        "driftCorrectedTrack": [[round(float(a), 5), round(float(b), 5)] for a, b in frame.lonlat(q[::step])],
    }


def attribute(slick_geom, slick_pixels_lonlat: np.ndarray, metrics: dict, acquired: datetime, met: dict,
              tracks: list[dict], sar_targets: list[dict], lookback_h: float = 24.0) -> dict:
    """Rank candidate sources for one slick. `tracks`: [{mmsi, name, shipType, points:[{ts, lon, lat, sog, cog}]}]."""
    c = slick_geom.centroid
    frame = LocalFrame(c.x, c.y)
    drift = drift_velocity(met)
    pts = slick_pixels_lonlat
    if len(pts) > 2500:
        pts = pts[np.random.default_rng(0).choice(len(pts), 2500, replace=False)]
    slick_xy = frame.xy(pts[:, 0], pts[:, 1])
    # SAR targets within 10 km of any part of the slick are candidates / head evidence.
    near_targets, static_near = [], []
    for t in sar_targets:
        txy = frame.xy([t["lon"]], [t["lat"]])[0]
        if float(np.min(np.linalg.norm(slick_xy - txy, axis=1))) < 10.0:
            (static_near if t.get("static") else near_targets).append(t)
    head = slick_head(metrics, drift, near_targets, frame)
    head_xy = frame.xy([head["lon"]], [head["lat"]])[0]
    tail_xy = frame.xy([head["tail"]["lon"]], [head["tail"]["lat"]])[0]

    candidates = []
    n_positions = 0
    for tr in tracks:
        n_positions += len(tr["points"])
        res = _score_track(tr, acquired, drift, frame, slick_xy, head_xy, tail_xy, head["confidence"], lookback_h)
        if not res or res["score"] <= 0:
            continue
        res.update({"kind": "ais_vessel", "name": tr.get("name"), "shipType": tr.get("shipType"),
                    "category": ship_category(tr.get("shipType")), "flag": tr.get("flag"),
                    "track": [[p["lon"], p["lat"], p["ts"].isoformat()] for p in
                              tr["points"][:: max(1, len(tr["points"]) // 200)]]})
        candidates.append(res)

    # Coincident SAR targets: cross-match with AIS at T; score by proximity to the head.
    for t in near_targets:
        txy = frame.xy([t["lon"]], [t["lat"]])[0]
        d_head = float(np.linalg.norm(txy - head_xy))
        d_slick = float(np.min(np.linalg.norm(slick_xy - txy, axis=1)))
        match = _ais_match(tracks, acquired, frame, txy)
        score = math.exp(-d_head / 1.5) * (0.9 if d_slick < 1.0 else 0.6)
        if score < 0.05:
            continue
        cand = {
            "kind": "sar_vessel", "lon": t["lon"], "lat": t["lat"], "peakDb": t["peakDb"],
            "extentM": t.get("extentM"), "distanceToHeadKm": round(d_head, 2),
            "distanceToSlickKm": round(d_slick, 2), "score": round(score, 4),
            "aisMatch": None, "darkVessel": match is None,
        }
        if match is not None:
            tr, dm = match
            cand["aisMatch"] = {"mmsi": tr["mmsi"], "name": tr.get("name"), "offsetKm": round(dm, 2)}
            # Boost the AIS candidate — the ship is literally on the slick head in the image.
            for a in candidates:
                if a["mmsi"] == tr["mmsi"]:
                    a["score"] = round(a["score"] + 0.5 * score, 4)
                    a["coincidentSarTarget"] = True
        candidates.append(cand)

    # "Recent vessel": a ship that stopped discharging and kept going is now ahead of the slick head,
    # close to the extension of the slick's local axis (speed x time since release).
    seen = {(c["lon"], c["lat"]) for c in candidates if c["kind"] == "sar_vessel"}
    L = float(np.linalg.norm(tail_xy - head_xy))
    near_head = slick_xy[np.linalg.norm(slick_xy - head_xy, axis=1) < max(5.0, 0.15 * L)]
    if len(near_head) >= 5:
        centre = near_head.mean(0)
        axis = np.linalg.svd(near_head - centre, full_matrices=False)[2][0]
        if np.dot(head_xy - centre, axis) < 0:
            axis = -axis
    else:
        axis = (head_xy - tail_xy) / max(L, 1e-6)
    cos_max = math.cos(math.radians(25))
    for t in sar_targets:
        if t.get("static") or (t["lon"], t["lat"]) in seen:
            continue
        v = frame.xy([t["lon"]], [t["lat"]])[0] - head_xy
        dist = float(np.linalg.norm(v))
        if dist < 2.0 or dist > 60.0:
            continue
        cos = float(np.dot(v, axis) / dist)
        if cos < cos_max:
            continue
        score = 0.45 * math.exp(-dist / 25.0) * (cos - cos_max) / (1 - cos_max)
        if score < 0.03:
            continue
        match = _ais_match(tracks, acquired, frame, frame.xy([t["lon"]], [t["lat"]])[0])
        cand = {"kind": "sar_vessel", "relation": "ahead_on_axis", "lon": t["lon"], "lat": t["lat"],
                "peakDb": t["peakDb"], "extentM": t.get("extentM"), "distanceToHeadKm": round(dist, 2),
                "angleFromAxisDeg": round(math.degrees(math.acos(min(1.0, cos))), 1), "score": round(score, 4),
                "static": t.get("static"), "aisMatch": None, "darkVessel": match is None}
        if match is not None:
            cand["aisMatch"] = {"mmsi": match[0]["mmsi"], "name": match[0].get("name"), "offsetKm": round(match[1], 2)}
        candidates.append(cand)

    # Static bright structures (islands, rocks, platforms) at a slick end: a platform can be a source,
    # an island cannot, and SAR alone can't tell them apart — so they rank below vessels.
    for t in static_near:
        txy = frame.xy([t["lon"]], [t["lat"]])[0]
        d = min(float(np.linalg.norm(txy - head_xy)), float(np.linalg.norm(txy - tail_xy)))
        if d < 3.0 and not any(c["kind"] == "static_structure" and abs(c["lon"] - t["lon"]) < 0.02 and abs(c["lat"] - t["lat"]) < 0.02
                               for c in candidates):
            candidates.append({"kind": "static_structure", "name": "Static structure (island, rock or platform)",
                               "lon": t["lon"], "lat": t["lat"], "peakDb": t["peakDb"], "distanceToEndKm": round(d, 2),
                               "reason": t.get("staticReason"), "score": round(0.25 * math.exp(-d / 1.5), 4)})

    for p in PLATFORMS:
        pxy = frame.xy([p["lon"]], [p["lat"]])[0]
        d = min(float(np.linalg.norm(pxy - head_xy)), float(np.linalg.norm(pxy - tail_xy)))
        if d < 5.0:
            candidates.append({"kind": "infrastructure", "id": p["id"], "name": p["name"], "lon": p["lon"],
                               "lat": p["lat"], "distanceToEndKm": round(d, 2),
                               "score": round(0.8 * math.exp(-d / 2.0), 4)})

    candidates.sort(key=lambda x: x["score"], reverse=True)
    # Confidence: soft-max against a 'no identifiable source' baseline.
    baseline = 0.08
    z = sum(math.exp(6 * cnd["score"]) for cnd in candidates[:10]) + math.exp(6 * baseline)
    for cnd in candidates:
        cnd["confidence"] = round(math.exp(6 * cnd["score"]) / z, 3)
    culprit = candidates[0] if candidates and candidates[0]["score"] > baseline else None
    vessels_in_window = len(tracks)
    return {
        "method": "AIS drift back-propagation + coincident SAR vessel detection + infrastructure proximity",
        "acquiredAt": acquired.isoformat(),
        "drift": {"eastMs": round(drift[0], 4), "northMs": round(drift[1], 4),
                  "speedKnots": round(math.hypot(*drift) * 1.94384, 3)},
        "head": head,
        "aisCoverage": {"vessels": vessels_in_window, "positions": n_positions, "lookbackHours": lookback_h},
        "candidates": candidates[:15],
        "culprit": culprit,
    }
