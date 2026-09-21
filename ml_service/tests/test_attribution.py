"""Attribution on a controlled scenario: the discharging tanker must outrank ships that merely pass by."""
import math
from datetime import datetime, timedelta, timezone

import numpy as np
from shapely.geometry import MultiPoint

from marinesight.attribution import attribute

T = datetime(2026, 8, 7, 14, 22, tzinfo=timezone.utc)
LAT0, LON0 = 17.8, 56.8
KX = 111.32 * math.cos(math.radians(LAT0))
MET = {"windMs": 8.0, "windFromDeg": 225.0, "currentMs": 0.25, "currentToDeg": 60.0, "waveHeightM": 2.0}


def drift():
    u = 0.25 * math.sin(math.radians(60)) + 0.03 * 8 * math.sin(math.radians(45))
    v = 0.25 * math.cos(math.radians(60)) + 0.03 * 8 * math.cos(math.radians(45))
    return u, v


def track(start_lon, start_lat, heading_deg, knots, hours, step_min=10):
    pts = []
    for k in range(int(hours * 60 / step_min) + 1):
        t = T - timedelta(hours=hours) + timedelta(minutes=k * step_min)
        d_km = knots * 1.852 * (k * step_min / 60)
        lon = start_lon + d_km * math.sin(math.radians(heading_deg)) / KX
        lat = start_lat + d_km * math.cos(math.radians(heading_deg)) / 111.32
        pts.append({"ts": t, "lon": lon, "lat": lat, "sog": knots, "cog": heading_deg})
    return pts


def slick_from_discharge(points, t_on, t_off):
    """Pixels of oil released along `points` between t_on and t_off, advected to T."""
    u, v = drift()
    out = []
    rng = np.random.default_rng(1)
    for p in points:
        if not (t_on <= p["ts"] <= t_off):
            continue
        age = (T - p["ts"]).total_seconds()
        lon = p["lon"] + u * age / 1000 / KX
        lat = p["lat"] + v * age / 1000 / 111.32
        for _ in range(12):  # spread
            out.append([lon + rng.normal(0, 0.15) / KX, lat + rng.normal(0, 0.15) / 111.32])
    return np.array(out)


def test_discharging_tanker_ranks_first():
    culprit_pts = track(LON0, LAT0, 250, 12, 8)  # heading WSW
    passer_pts = track(LON0 + 0.25, LAT0 - 0.3, 330, 14, 8)  # crosses the area later
    slick_px = slick_from_discharge(culprit_pts, T - timedelta(hours=5), T - timedelta(minutes=20))
    geom = MultiPoint(slick_px.tolist()).convex_hull.buffer(0.002)
    # Endpoints: the head (youngest oil) is at the western end in this scenario.
    ends = [slick_px[np.argmin(slick_px[:, 0])].tolist(), slick_px[np.argmax(slick_px[:, 0])].tolist()]
    metrics = {
        "centroid": [geom.centroid.x, geom.centroid.y], "lengthKm": 60.0, "endpoints": ends,
        "endProfiles": [{"widthKm": 0.4, "meanDb": -24.0, "pixels": 30}, {"widthKm": 1.5, "meanDb": -21.0, "pixels": 120}],
    }
    tracks = [
        {"mmsi": "419000001", "name": "CULPRIT TANKER", "shipType": 80, "points": culprit_pts},
        {"mmsi": "235000002", "name": "PASSING CARGO", "shipType": 70, "points": passer_pts},
    ]
    res = attribute(geom, slick_px, metrics, T, MET, tracks, sar_targets=[])
    assert res["culprit"]["mmsi"] == "419000001"
    top, second = res["candidates"][0], res["candidates"][1] if len(res["candidates"]) > 1 else {"score": 0}
    assert top["coverage"] > 0.8
    assert top["meanOffsetKm"] < 1.0
    assert top["score"] > 2 * second["score"]
    # Estimated release window brackets the true discharge (±40 min).
    t_first = datetime.fromisoformat(top["releaseWindow"][0])
    assert abs((t_first - (T - timedelta(hours=5))).total_seconds()) < 2400


def test_coincident_sar_vessel_flags_dark_ship_and_sets_head():
    culprit_pts = track(LON0, LAT0, 250, 12, 6)
    slick_px = slick_from_discharge(culprit_pts, T - timedelta(hours=4), T)
    geom = MultiPoint(slick_px.tolist()).convex_hull.buffer(0.002)
    west = slick_px[np.argmin(slick_px[:, 0])].tolist()
    east = slick_px[np.argmax(slick_px[:, 0])].tolist()
    # Same widths/darkness at both ends: only the SAR target can decide the head.
    metrics = {"centroid": [geom.centroid.x, geom.centroid.y], "lengthKm": 50.0, "endpoints": [east, west],
               "endProfiles": [{"widthKm": 1.0, "meanDb": -22.0, "pixels": 50}] * 2}
    ship_at_T = culprit_pts[-1]
    sar = [{"lon": ship_at_T["lon"], "lat": ship_at_T["lat"], "peakDb": 4.0, "pixels": 6, "extentM": 220}]
    res = attribute(geom, slick_px, metrics, T, MET, tracks=[], sar_targets=sar)
    assert abs(res["head"]["lon"] - west[0]) < 1e-9
    top = res["culprit"]
    assert top["kind"] == "sar_vessel" and top["darkVessel"] is True
    assert top["distanceToHeadKm"] < 1.5


def test_vessel_ahead_on_slick_axis_is_a_recent_vessel_candidate():
    """Discharge stopped 90 min before the pass; the ship steamed on and is ~33 km past the head."""
    pts = track(LON0, LAT0, 250, 12, 8)
    slick_px = slick_from_discharge(pts, T - timedelta(hours=6), T - timedelta(minutes=90))
    geom = MultiPoint(slick_px.tolist()).convex_hull.buffer(0.002)
    west = slick_px[np.argmin(slick_px[:, 0])].tolist()
    east = slick_px[np.argmax(slick_px[:, 0])].tolist()
    metrics = {"centroid": [geom.centroid.x, geom.centroid.y], "lengthKm": 100.0, "endpoints": [east, west],
               "endProfiles": [{"widthKm": 2.0, "meanDb": -21.0, "pixels": 80}, {"widthKm": 0.4, "meanDb": -24.0, "pixels": 20}]}
    ship = pts[-1]
    off_axis = {"lon": LON0 + 0.2, "lat": LAT0 - 0.4, "peakDb": 6.0, "pixels": 4, "extentM": 150, "static": False}
    sar = [{"lon": ship["lon"], "lat": ship["lat"], "peakDb": 5.0, "pixels": 4, "extentM": 220, "static": False}, off_axis]
    res = attribute(geom, slick_px, metrics, T, MET, tracks=[], sar_targets=sar)
    ahead = [c for c in res["candidates"] if c.get("relation") == "ahead_on_axis"]
    assert len(ahead) == 1 and abs(ahead[0]["lon"] - ship["lon"]) < 1e-9
    assert 20 < ahead[0]["distanceToHeadKm"] < 45
    assert res["culprit"]["lon"] == ship["lon"]
