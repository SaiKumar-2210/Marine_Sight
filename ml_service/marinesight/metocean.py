"""MetOcean context at the SAR acquisition time: 10 m wind (ERA5 / forecast), currents and waves."""
from __future__ import annotations

import math
import time
from datetime import datetime, timedelta, timezone
from functools import lru_cache

import requests

ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
FORECAST = "https://api.open-meteo.com/v1/forecast"
MARINE = "https://marine-api.open-meteo.com/v1/marine"

_session = requests.Session()


def _get(url: str, params: dict) -> dict:
    delay = 1.5
    for _ in range(4):
        try:
            r = _session.get(url, params=params, timeout=30)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 429:
                time.sleep(delay * 4)
        except requests.RequestException:
            pass
        time.sleep(delay)
        delay *= 2
    return {}


def _hour_value(payload: dict, var: str, when: datetime):
    hourly = payload.get("hourly") or {}
    times, vals = hourly.get("time") or [], hourly.get(var) or []
    key = when.strftime("%Y-%m-%dT%H:00")
    if key in times:
        v = vals[times.index(key)]
        if v is not None:
            return float(v)
    return None


@lru_cache(maxsize=4096)
def _fetch(lat: float, lon: float, date: str, recent: bool) -> tuple[dict, dict]:
    common = {"latitude": lat, "longitude": lon, "start_date": date, "end_date": date, "timezone": "GMT"}
    wind = _get(FORECAST if recent else ARCHIVE, {**common, "hourly": "wind_speed_10m,wind_direction_10m",
                                                  "wind_speed_unit": "ms"})
    if not (wind.get("hourly") or {}).get("wind_speed_10m") or all(
            v is None for v in wind["hourly"]["wind_speed_10m"]):
        # ERA5 lags ~5 days; fall back to the forecast archive for recent dates.
        wind = _get(FORECAST, {**common, "hourly": "wind_speed_10m,wind_direction_10m", "wind_speed_unit": "ms"})
    marine = _get(MARINE, {**common, "hourly": "wave_height,ocean_current_velocity,ocean_current_direction"})
    return wind, marine


def metocean_at(lat: float, lon: float, when: datetime) -> dict:
    """Wind (m/s, direction FROM), current (m/s, direction TOWARDS), significant wave height (m)."""
    when = when.astimezone(timezone.utc)
    # Round to the nearest hour.
    if when.minute >= 30:
        when = when.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    else:
        when = when.replace(minute=0, second=0, microsecond=0)
    recent = (datetime.now(timezone.utc) - when).days < 6
    wind, marine = _fetch(round(lat, 2), round(lon, 2), when.strftime("%Y-%m-%d"), recent)
    wind_ms = _hour_value(wind, "wind_speed_10m", when)
    wind_dir = _hour_value(wind, "wind_direction_10m", when)
    cur_kmh = _hour_value(marine, "ocean_current_velocity", when)  # Open-Meteo reports km/h
    cur_dir = _hour_value(marine, "ocean_current_direction", when)
    wave = _hour_value(marine, "wave_height", when)
    cur_ms = None if cur_kmh is None else cur_kmh / 3.6
    return {
        "time": when.isoformat(),
        "windMs": wind_ms,
        "windFromDeg": wind_dir,
        "currentMs": cur_ms,
        "currentToDeg": cur_dir,
        "waveHeightM": wave,
        "source": "Open-Meteo (ERA5 reanalysis / ECMWF forecast; Copernicus Marine currents & waves)",
    }


def drift_velocity(met: dict, windage: float = 0.03) -> tuple[float, float]:
    """Surface oil drift (east, north) in m/s: current + 3% of wind (wind blows FROM windFromDeg)."""
    u = v = 0.0
    if met.get("currentMs") is not None and met.get("currentToDeg") is not None:
        a = math.radians(met["currentToDeg"])
        u += met["currentMs"] * math.sin(a)
        v += met["currentMs"] * math.cos(a)
    if met.get("windMs") is not None and met.get("windFromDeg") is not None:
        a = math.radians(met["windFromDeg"] + 180.0)
        u += windage * met["windMs"] * math.sin(a)
        v += windage * met["windMs"] * math.cos(a)
    return u, v
