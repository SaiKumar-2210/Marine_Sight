"""Stage 3: wind / wave / current feasibility (SAR oil window)."""
from __future__ import annotations

from datetime import datetime

import requests

# Oil damps Bragg waves only in a limited wind window.
# < ~2.5 m/s: whole sea is dark (look-alike). > ~14 m/s: oil mixed into waves.
WIND_MIN_MS = 2.5
WIND_MAX_MS = 14.0
WIND_HARD_MIN = 2.0
WIND_HARD_MAX = 16.0


def _compass(deg: float) -> str:
    dirs = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    return dirs[int((deg + 22.5) // 45) % 8]


def _pick_hourly(hourly: dict, date_str: str, hour: int = 12) -> dict:
    times = hourly.get("time") or []
    target = f"{date_str}T{hour:02d}:00"
    idx = 0
    for i, t in enumerate(times):
        if t.startswith(date_str):
            idx = i
            if t.startswith(target):
                break
    out = {"time": times[idx] if times else date_str}
    for key, vals in hourly.items():
        if key == "time":
            continue
        if isinstance(vals, list) and idx < len(vals):
            out[key] = vals[idx]
    return out


def fetch_metocean(lat: float, lng: float, date_str: str) -> dict:
    today = datetime.utcnow().strftime("%Y-%m-%d")
    historical = date_str < today

    if historical:
        wind_url = (
            "https://archive-api.open-meteo.com/v1/archive"
            f"?latitude={lat}&longitude={lng}&start_date={date_str}&end_date={date_str}"
            "&hourly=wind_speed_10m,wind_direction_10m&wind_speed_unit=ms"
        )
        marine_url = (
            "https://marine-api.open-meteo.com/v1/marine"
            f"?latitude={lat}&longitude={lng}&start_date={date_str}&end_date={date_str}"
            "&hourly=wave_height,wave_direction,ocean_current_velocity,ocean_current_direction"
        )
    else:
        wind_url = (
            "https://api.open-meteo.com/v1/forecast"
            f"?latitude={lat}&longitude={lng}"
            "&current=wind_speed_10m,wind_direction_10m&wind_speed_unit=ms"
        )
        marine_url = (
            "https://marine-api.open-meteo.com/v1/marine"
            f"?latitude={lat}&longitude={lng}"
            "&current=wave_height,wave_direction,ocean_current_velocity,ocean_current_direction"
        )

    wind_ms = None
    wind_dir = None
    wave_h = None
    wave_dir = None
    curr_ms = None
    curr_dir = None
    source = "open-meteo"

    try:
        w = requests.get(wind_url, timeout=12).json()
        if historical:
            row = _pick_hourly(w.get("hourly") or {}, date_str)
            wind_ms = row.get("wind_speed_10m")
            wind_dir = row.get("wind_direction_10m")
        else:
            cur = w.get("current") or {}
            wind_ms = cur.get("wind_speed_10m")
            wind_dir = cur.get("wind_direction_10m")
    except Exception:
        source = "open-meteo-partial"

    try:
        m = requests.get(marine_url, timeout=12).json()
        if historical:
            row = _pick_hourly(m.get("hourly") or {}, date_str)
            wave_h = row.get("wave_height")
            wave_dir = row.get("wave_direction")
            curr_ms = row.get("ocean_current_velocity")
            curr_dir = row.get("ocean_current_direction")
        else:
            cur = m.get("current") or {}
            wave_h = cur.get("wave_height")
            wave_dir = cur.get("wave_direction")
            curr_ms = cur.get("ocean_current_velocity")
            curr_dir = cur.get("ocean_current_direction")
    except Exception:
        pass

    wind_ms = float(wind_ms) if wind_ms is not None else None
    wind_dir = float(wind_dir) if wind_dir is not None else None
    wave_h = float(wave_h) if wave_h is not None else None
    curr_ms = float(curr_ms) if curr_ms is not None else None
    curr_dir = float(curr_dir) if curr_dir is not None else None
    wave_dir = float(wave_dir) if wave_dir is not None else None

    window = "unknown"
    score = 50.0
    note = "Wind unavailable — metocean gate inconclusive"
    if wind_ms is not None:
        if wind_ms < WIND_HARD_MIN or wind_ms > WIND_HARD_MAX:
            window = "fail"
            score = 12.0 if wind_ms < WIND_HARD_MIN else 18.0
            note = (
                f"Wind {wind_ms:.1f} m/s outside SAR oil window "
                f"({WIND_HARD_MIN:.0f}–{WIND_HARD_MAX:.0f} m/s) — likely look-alike"
            )
        elif wind_ms < WIND_MIN_MS or wind_ms > WIND_MAX_MS:
            window = "marginal"
            score = 48.0
            note = f"Wind {wind_ms:.1f} m/s is marginal for SAR oil detection"
        else:
            window = "valid"
            # Peak detectability around 5–8 m/s.
            dist = abs(wind_ms - 6.5) / 6.5
            score = float(max(70.0, 96.0 - 30.0 * dist))
            note = f"Wind {wind_ms:.1f} m/s inside SAR oil window (2.5–14 m/s)"

    if wave_h is not None and wave_h > 4.0 and window != "fail":
        score = min(score, 42.0)
        window = "marginal" if window == "valid" else window
        note += f"; significant wave height {wave_h:.1f} m may mix a thin slick"

    wind_kn = None if wind_ms is None else round(wind_ms * 1.94384, 1)
    curr_kn = None if curr_ms is None else round(curr_ms * 1.94384, 2)
    drift_kn = None
    if curr_kn is not None and wind_kn is not None:
        drift_kn = round(curr_kn + 0.03 * wind_kn, 2)
    elif curr_kn is not None:
        drift_kn = curr_kn

    return {
        "source": source,
        "windMs": None if wind_ms is None else round(wind_ms, 2),
        "windKnots": wind_kn,
        "windDirDeg": None if wind_dir is None else round(wind_dir, 0),
        "windDir": None if wind_dir is None else _compass(wind_dir),
        "waveHeightM": None if wave_h is None else round(wave_h, 2),
        "waveDirDeg": None if wave_dir is None else round(wave_dir, 0),
        "currentMs": None if curr_ms is None else round(curr_ms, 3),
        "currentKnots": curr_kn,
        "currentDirDeg": None if curr_dir is None else round(curr_dir, 0),
        "currentDir": None if curr_dir is None else _compass(curr_dir),
        "driftKnots": drift_kn,
        "window": window,
        "score": round(score, 1),
        "note": note,
        "formula": "V_slick = V_current + 0.03 * V_wind",
    }
