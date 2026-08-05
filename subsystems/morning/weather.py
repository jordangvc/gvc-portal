"""
Morning Brief weather — Open-Meteo (free, no API key) for the employee's
starting location, plus a short drywall drying / exposed-work tip.
=========================================================================
No new deps: stdlib urllib + json only. Soft-fails to a label-only payload
when coords are missing or the fetch fails — never raises into the brief.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Optional

# WMO weather interpretation codes → short plain-English condition.
# https://open-meteo.com/en/docs
_WEATHERCODE_LABELS: dict[int, str] = {
    0: "clear",
    1: "mainly clear",
    2: "partly cloudy",
    3: "cloudy",
    45: "foggy",
    48: "foggy",
    51: "drizzle",
    53: "drizzle",
    55: "drizzle",
    56: "freezing drizzle",
    57: "freezing drizzle",
    61: "rain",
    63: "rain",
    65: "heavy rain",
    66: "freezing rain",
    67: "freezing rain",
    71: "snow",
    73: "snow",
    75: "heavy snow",
    77: "snow",
    80: "rain showers",
    81: "rain showers",
    82: "heavy rain showers",
    85: "snow showers",
    86: "snow showers",
    95: "thunderstorm",
    96: "thunderstorm",
    99: "thunderstorm",
}

_WET_CODES = frozenset({
    51, 53, 55, 56, 57, 61, 63, 65, 66, 67,
    71, 73, 75, 77, 80, 81, 82, 85, 86, 95, 96, 99,
})
_RAIN_CODES = frozenset({
    51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82, 95, 96, 99,
})
_SNOW_CODES = frozenset({71, 73, 75, 77, 85, 86})


def condition_from_weathercode(code: Optional[int]) -> Optional[str]:
    """PURE. WMO weathercode → short condition label, or None."""
    if code is None:
        return None
    try:
        return _WEATHERCODE_LABELS.get(int(code))
    except (TypeError, ValueError):
        return None


def drying_note(*, condition: Optional[str], weathercode: Optional[int],
                humidity_pct: Optional[float],
                precip_in: Optional[float] = None,
                precip_probability: Optional[float] = None) -> Optional[str]:
    """
    PURE. One short tip for drywall crews — protect board / mud dry speed.
    No safety lectures. None when there's nothing useful to say.
    """
    code = None
    if weathercode is not None:
        try:
            code = int(weathercode)
        except (TypeError, ValueError):
            code = None

    wet = False
    if code is not None and code in _WET_CODES:
        wet = True
    if precip_in is not None and float(precip_in) >= 0.01:
        wet = True
    if precip_probability is not None and float(precip_probability) >= 50:
        wet = True

    if wet:
        if code is not None and code in _SNOW_CODES:
            return "Snow — protect exposed board; mud dries slowly."
        return "Rain likely — protect exposed board; expect slow mud dry."

    hum = None
    if humidity_pct is not None:
        try:
            hum = float(humidity_pct)
        except (TypeError, ValueError):
            hum = None

    cond = (condition or "").lower()
    clearish = cond in ("clear", "mainly clear") or code == 0

    if hum is not None and hum >= 70:
        return "High humidity — expect longer mud dry times."
    if clearish and hum is not None and hum <= 50:
        return "Clear and dry — good drying day for mud."
    if clearish and hum is None:
        return "Clear skies — decent drying if humidity stays down."
    return None


def build_weather_payload(origin: dict, *, api_payload: dict) -> dict:
    """
    PURE. Shape an Open-Meteo JSON body into the brief's weather dict.
    Keys: label, temp_f, summary (compat) + condition, humidity_pct,
    precip_in, precip_probability, drying_note (optional).
    """
    label = (origin or {}).get("label") or "Local"
    current = api_payload.get("current") or {}
    # Legacy current_weather=true shape still accepted.
    legacy = api_payload.get("current_weather") or {}

    temp = current.get("temperature_2m")
    if temp is None:
        temp = legacy.get("temperature")

    code = current.get("weather_code")
    if code is None:
        code = legacy.get("weathercode")
    try:
        code_i = int(code) if code is not None else None
    except (TypeError, ValueError):
        code_i = None

    condition = condition_from_weathercode(code_i)

    humidity = current.get("relative_humidity_2m")
    try:
        humidity_f = float(humidity) if humidity is not None else None
    except (TypeError, ValueError):
        humidity_f = None

    precip = current.get("precipitation")
    try:
        precip_f = float(precip) if precip is not None else None
    except (TypeError, ValueError):
        precip_f = None

    # Prefer current precip probability; else first hourly slot if present.
    precip_prob = current.get("precipitation_probability")
    if precip_prob is None:
        hourly = api_payload.get("hourly") or {}
        probs = hourly.get("precipitation_probability") or []
        if probs:
            precip_prob = probs[0]
    try:
        precip_prob_f = float(precip_prob) if precip_prob is not None else None
    except (TypeError, ValueError):
        precip_prob_f = None

    tip = drying_note(
        condition=condition,
        weathercode=code_i,
        humidity_pct=humidity_f,
        precip_in=precip_f,
        precip_probability=precip_prob_f,
    )

    if temp is not None and condition:
        summary = f"{temp}°F · {condition}"
    elif temp is not None:
        summary = f"{temp}°F"
    elif condition:
        summary = condition
    else:
        summary = None

    out: dict[str, Any] = {
        "label": label,
        "temp_f": temp,
        "summary": summary,
    }
    if condition is not None:
        out["condition"] = condition
    if humidity_f is not None:
        out["humidity_pct"] = round(humidity_f)
    if precip_f is not None:
        out["precip_in"] = precip_f
    if precip_prob_f is not None:
        out["precip_probability"] = round(precip_prob_f)
    if tip:
        out["drying_note"] = tip
    return out


def _fetch_open_meteo(lat: float, lng: float, *, timeout: float = 3.0) -> dict:
    """Network. Open-Meteo current + next-hour precip probability."""
    url = (
        "https://api.open-meteo.com/v1/forecast?"
        f"latitude={lat}&longitude={lng}"
        "&current=temperature_2m,relative_humidity_2m,precipitation,"
        "weather_code,precipitation_probability"
        "&hourly=precipitation_probability"
        "&forecast_hours=1"
        "&temperature_unit=fahrenheit"
        "&precipitation_unit=inch"
        "&timezone=America%2FNew_York"
    )
    req = urllib.request.Request(url, headers={"User-Agent": "gvc-portal/morning"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def weather_for_origin(origin: Optional[dict]) -> dict:
    """
    Fetch weather for an origin dict. Returns a soft payload (never raises).
    Backward-compatible keys: label, temp_f, summary.
    """
    origin = origin or {}
    label = origin.get("label") or "Local"
    lat, lng = origin.get("lat"), origin.get("lng")
    if lat is None or lng is None:
        return {"label": label, "summary": None}
    try:
        data = _fetch_open_meteo(float(lat), float(lng))
        return build_weather_payload(origin, api_payload=data)
    except (urllib.error.URLError, TimeoutError, ValueError, OSError, json.JSONDecodeError):
        return {"label": label, "summary": None}
    except Exception:  # noqa: BLE001 — brief must never crash on weather
        return {"label": label, "summary": None}
