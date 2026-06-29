"""Wetter-Datenquelle (Open-Meteo Forecast API) für Sirdar — KONZEPT §4/§7, Phase 2A.

Liefert einen kompakten, JSON-serialisierbaren Wetter-Forecast für den
Planungshorizont, damit der Coach Outdoor-/Indoor- und Routen-Entscheidungen
treffen kann (Regen/Wind → Indoor erwägen bzw. Routenexposition beachten).

Open-Meteo Forecast API (kein API-Key für nicht-kommerzielle Nutzung):
    Endpoint: https://api.open-meteo.com/v1/forecast
    Quelle:   https://open-meteo.com/en/docs
    - stündlich: temperature_2m, precipitation, precipitation_probability,
      wind_speed_10m, wind_gusts_10m, wind_direction_10m
    - täglich:   temperature_2m_max/min, precipitation_sum,
      precipitation_probability_max, wind_speed_10m_max
    - timezone=auto (Open-Meteo wählt anhand der Koordinaten), forecast_days.

Designprinzip (KONZEPT §6.3): keine Werte erfinden. Fehlt das Wetter (deaktiviert,
keine Koordinaten, Netzfehler), liefert die Quelle ``None`` — der Coach plant dann
ohne Wetter weiter (Plan-Generierung darf NIE am Wetter scheitern).
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from src.config import load_settings

logger = logging.getLogger(__name__)

SOURCE_NAME = "weather_open_meteo"

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

# Open-Meteo erlaubt bis zu 16 Forecast-Tage.
_MAX_FORECAST_DAYS = 16
_DEFAULT_DAYS = 10
_HTTP_TIMEOUT_S = 15.0

_HOURLY_VARS = (
    "temperature_2m",
    "precipitation",
    "precipitation_probability",
    "wind_speed_10m",
    "wind_gusts_10m",
    "wind_direction_10m",
)
_DAILY_VARS = (
    "temperature_2m_max",
    "temperature_2m_min",
    "precipitation_sum",
    "precipitation_probability_max",
    "wind_speed_10m_max",
)

# 8 Kompass-Sektoren für eine grobe, menschenlesbare Windrichtung.
_COMPASS = ("N", "NO", "O", "SO", "S", "SW", "W", "NW")


# ─── Helfer ──────────────────────────────────────────────────────────────

def _compass(degrees: float | None) -> str | None:
    """Windrichtung in Grad → grober Kompass-String (N/NO/O/…). None bleibt None."""
    if degrees is None:
        return None
    idx = int((degrees % 360) / 45.0 + 0.5) % 8
    return _COMPASS[idx]


def _dominant_wind_direction(directions: list[float | None]) -> str | None:
    """Vorherrschende Windrichtung aus stündlichen Grad-Werten (häufigster Sektor)."""
    counts: dict[str, int] = {}
    for d in directions:
        sector = _compass(d)
        if sector is not None:
            counts[sector] = counts.get(sector, 0) + 1
    if not counts:
        return None
    return max(counts, key=counts.get)  # type: ignore[arg-type]


def _round_or_none(value: Any, ndigits: int = 1) -> float | None:
    try:
        return round(float(value), ndigits)
    except (TypeError, ValueError):
        return None


def _hours_for_day(hourly: dict, day: str) -> list[dict]:
    """Extrahiert die stündlichen Werte für ein bestimmtes Datum (YYYY-MM-DD)."""
    times = hourly.get("time") or []
    hours: list[dict] = []
    for i, ts in enumerate(times):
        if not isinstance(ts, str) or not ts.startswith(day):
            continue
        # ts ist 'YYYY-MM-DDTHH:MM' — wir behalten nur die Stunde.
        hour_label = ts[11:16] if len(ts) >= 16 else ts
        hours.append(
            {
                "time": hour_label,
                "temp": _hourly_value(hourly, "temperature_2m", i),
                "precip": _hourly_value(hourly, "precipitation", i),
                "precip_prob": _hourly_value(hourly, "precipitation_probability", i),
                "wind": _hourly_value(hourly, "wind_speed_10m", i),
                "wind_gust": _hourly_value(hourly, "wind_gusts_10m", i),
                "wind_dir": _compass(_hourly_value(hourly, "wind_direction_10m", i)),
            }
        )
    return hours


def _hourly_value(hourly: dict, var: str, idx: int):
    series = hourly.get(var) or []
    return series[idx] if idx < len(series) else None


def _summarize(data: dict, include_hours: bool) -> dict:
    """Baut die kompakte Tages-Zusammenfassung aus der Open-Meteo-Antwort."""
    daily = data.get("daily") or {}
    hourly = data.get("hourly") or {}
    dates = daily.get("time") or []

    days: list[dict] = []
    for i, day in enumerate(dates):
        # Vorherrschende Windrichtung aus den Stunden dieses Tages.
        dir_series = hourly.get("wind_direction_10m") or []
        times = hourly.get("time") or []
        day_dirs = [
            dir_series[j]
            for j, ts in enumerate(times)
            if isinstance(ts, str) and ts.startswith(day) and j < len(dir_series)
        ]
        entry = {
            "date": day,
            "temp_max": _daily_value(daily, "temperature_2m_max", i),
            "temp_min": _daily_value(daily, "temperature_2m_min", i),
            "precipitation_sum": _daily_value(daily, "precipitation_sum", i),
            "precip_probability_max": _daily_value(daily, "precipitation_probability_max", i),
            "wind_max": _daily_value(daily, "wind_speed_10m_max", i),
            "wind_direction": _dominant_wind_direction(day_dirs),
        }
        if include_hours:
            entry["hours"] = _hours_for_day(hourly, day)
        days.append(entry)

    units = data.get("daily_units") or {}
    return {
        "source": SOURCE_NAME,
        "latitude": data.get("latitude"),
        "longitude": data.get("longitude"),
        "timezone": data.get("timezone"),
        "units": {
            "temperature": units.get("temperature_2m_max", "°C"),
            "precipitation": units.get("precipitation_sum", "mm"),
            "wind_speed": units.get("wind_speed_10m_max", "km/h"),
        },
        "days": days,
    }


def _daily_value(daily: dict, var: str, idx: int):
    series = daily.get(var) or []
    return series[idx] if idx < len(series) else None


# ─── Öffentliche API ─────────────────────────────────────────────────────

def get_forecast(
    latitude: float,
    longitude: float,
    days: int = _DEFAULT_DAYS,
    include_hours: bool = True,
) -> dict | None:
    """Ruft den Open-Meteo-Forecast ab und liefert eine kompakte Struktur.

    Args:
        latitude/longitude: Koordinaten (Dezimalgrad).
        days: Forecast-Horizont in Tagen (1..16, wird begrenzt).
        include_hours: stündliche Detailwerte je Tag mitliefern (für Slot-Entscheidung).

    Returns:
        JSON-serialisierbares Dict (siehe ``_summarize``) oder ``None`` bei
        fehlenden Koordinaten / Netzwerk- bzw. API-Fehler. Wirft NIE — der Coach
        soll auch ohne Wetter planen können (Plan-Generierung > Wetter).
    """
    if latitude is None or longitude is None:
        logger.info("Open-Meteo: keine Koordinaten — überspringe Wetter-Forecast.")
        return None

    days = max(1, min(int(days or _DEFAULT_DAYS), _MAX_FORECAST_DAYS))
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "hourly": ",".join(_HOURLY_VARS),
        "daily": ",".join(_DAILY_VARS),
        "timezone": "auto",
        "forecast_days": days,
    }

    try:
        response = httpx.get(FORECAST_URL, params=params, timeout=_HTTP_TIMEOUT_S)
        response.raise_for_status()
        data = response.json()
    except (httpx.HTTPError, ValueError) as exc:  # ValueError: ungültiges JSON
        logger.warning("Open-Meteo-Forecast fehlgeschlagen: %s", exc)
        return None

    try:
        return _summarize(data, include_hours=include_hours)
    except Exception:  # noqa: BLE001 — Parsing darf den Coach nie crashen
        logger.warning("Open-Meteo-Antwort nicht verwertbar.", exc_info=True)
        return None


def forecast_from_settings(days: int = _DEFAULT_DAYS, include_hours: bool = True) -> dict | None:
    """Zieht lat/lon aus settings.integrations.weather_open_meteo und ruft get_forecast.

    Deaktiviert oder ohne Koordinaten → ``None`` (kein Fehler).
    """
    cfg = (load_settings().get("integrations", {}) or {}).get("weather_open_meteo", {}) or {}
    if not cfg.get("enabled"):
        logger.info("weather_open_meteo deaktiviert — kein Forecast.")
        return None
    latitude = cfg.get("latitude")
    longitude = cfg.get("longitude")
    if latitude is None or longitude is None:
        logger.info("weather_open_meteo aktiv, aber keine Koordinaten gesetzt — kein Forecast.")
        return None
    return get_forecast(latitude, longitude, days=days, include_hours=include_hours)


# ─── Source-Klasse (analog FileImportSource; konsistentes Adapter-Muster) ──

class WeatherSource:
    """Adapter für den Open-Meteo-Forecast (Wetter-Datenquelle).

    Nicht Teil des ``WorkoutSource``-Protocols (das liefert Aktivitäten); diese
    Quelle liefert Kontextdaten für die Plan-Logik, folgt aber demselben Stil
    (Modul-Funktionen + dünne Klasse) wie ``FileImportSource``.
    """

    name = SOURCE_NAME

    def __init__(self, latitude: float | None = None, longitude: float | None = None) -> None:
        self.latitude = latitude
        self.longitude = longitude

    def fetch(self, days: int = _DEFAULT_DAYS, include_hours: bool = True) -> dict | None:
        if self.latitude is None or self.longitude is None:
            return forecast_from_settings(days=days, include_hours=include_hours)
        return get_forecast(self.latitude, self.longitude, days=days, include_hours=include_hours)
