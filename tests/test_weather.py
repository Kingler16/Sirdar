"""Phase-2A Tests: Wetter-Datenquelle (src/data/sources/weather_open_meteo.py).

KEINE echten Netz-Calls: ``httpx.get`` wird gemonkeypatcht und liefert eine
kanonische Open-Meteo-Antwort. Geprüft werden: korrektes Parsen der Tages-/
Stunden-Struktur, disabled/keine Koordinaten → None, Netzfehler → None (nie Crash).
"""

from __future__ import annotations

import httpx
import pytest

from src.data.sources import weather_open_meteo as wx


# Kanonische Open-Meteo-Forecast-Antwort (gekürzt: 2 Tage, je 2 Stunden).
CANONICAL_RESPONSE = {
    "latitude": 48.1,
    "longitude": 11.5,
    "timezone": "Europe/Berlin",
    "hourly_units": {
        "temperature_2m": "°C",
        "precipitation": "mm",
        "wind_speed_10m": "km/h",
        "wind_direction_10m": "°",
    },
    "hourly": {
        "time": [
            "2026-06-29T08:00", "2026-06-29T09:00",
            "2026-06-30T08:00", "2026-06-30T09:00",
        ],
        "temperature_2m": [18.0, 20.0, 14.0, 15.0],
        "precipitation": [0.0, 0.5, 3.0, 4.0],
        "precipitation_probability": [10, 20, 80, 90],
        "wind_speed_10m": [12.0, 14.0, 30.0, 35.0],
        "wind_gusts_10m": [20.0, 22.0, 50.0, 55.0],
        "wind_direction_10m": [90, 95, 270, 265],  # O, O, W, W
    },
    "daily_units": {
        "temperature_2m_max": "°C",
        "precipitation_sum": "mm",
        "wind_speed_10m_max": "km/h",
    },
    "daily": {
        "time": ["2026-06-29", "2026-06-30"],
        "temperature_2m_max": [22.0, 16.0],
        "temperature_2m_min": [12.0, 9.0],
        "precipitation_sum": [0.5, 7.0],
        "precipitation_probability_max": [20, 90],
        "wind_speed_10m_max": [14.0, 35.0],
    },
}


class _FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("err", request=None, response=None)


@pytest.fixture()
def mock_httpx_ok(monkeypatch):
    """Patcht httpx.get auf die kanonische Antwort und merkt sich die Params."""
    calls = {}

    def fake_get(url, params=None, timeout=None, **kw):
        calls["url"] = url
        calls["params"] = params
        return _FakeResponse(CANONICAL_RESPONSE)

    monkeypatch.setattr(wx.httpx, "get", fake_get)
    return calls


# ─── Parsing ─────────────────────────────────────────────────────────────

def test_get_forecast_parses_daily(mock_httpx_ok):
    fc = wx.get_forecast(48.1, 11.5, days=2)
    assert fc is not None
    assert fc["source"] == "weather_open_meteo"
    assert fc["timezone"] == "Europe/Berlin"
    assert len(fc["days"]) == 2

    d0, d1 = fc["days"]
    assert d0["date"] == "2026-06-29"
    assert d0["temp_max"] == 22.0
    assert d0["temp_min"] == 12.0
    assert d0["precipitation_sum"] == 0.5
    assert d0["precip_probability_max"] == 20
    assert d0["wind_max"] == 14.0
    assert d0["wind_direction"] == "O"   # vorherrschend Ost
    assert d1["wind_direction"] == "W"   # vorherrschend West
    assert d1["precip_probability_max"] == 90


def test_get_forecast_includes_hours(mock_httpx_ok):
    fc = wx.get_forecast(48.1, 11.5, days=2, include_hours=True)
    d0 = fc["days"][0]
    assert "hours" in d0
    assert len(d0["hours"]) == 2
    h = d0["hours"][0]
    assert h["time"] == "08:00"
    assert h["temp"] == 18.0
    assert h["wind_dir"] == "O"


def test_get_forecast_can_skip_hours(mock_httpx_ok):
    fc = wx.get_forecast(48.1, 11.5, days=2, include_hours=False)
    assert "hours" not in fc["days"][0]


def test_get_forecast_sends_expected_params(mock_httpx_ok):
    wx.get_forecast(48.1, 11.5, days=5)
    params = mock_httpx_ok["params"]
    assert params["latitude"] == 48.1
    assert params["longitude"] == 11.5
    assert params["timezone"] == "auto"
    assert params["forecast_days"] == 5
    assert "temperature_2m" in params["hourly"]
    assert "precipitation_probability" in params["hourly"]
    assert "wind_gusts_10m" in params["hourly"]
    assert "temperature_2m_max" in params["daily"]
    assert "wind_speed_10m_max" in params["daily"]


def test_get_forecast_clamps_days(mock_httpx_ok):
    wx.get_forecast(48.1, 11.5, days=99)
    assert mock_httpx_ok["params"]["forecast_days"] == 16  # auf API-Maximum begrenzt


# ─── Robustheit ──────────────────────────────────────────────────────────

def test_get_forecast_no_coordinates_returns_none():
    assert wx.get_forecast(None, 11.5) is None
    assert wx.get_forecast(48.1, None) is None


def test_get_forecast_network_error_returns_none(monkeypatch):
    def boom(*a, **kw):
        raise httpx.ConnectError("no network")

    monkeypatch.setattr(wx.httpx, "get", boom)
    assert wx.get_forecast(48.1, 11.5) is None  # sauber None, kein Crash


def test_get_forecast_http_error_returns_none(monkeypatch):
    def fake_get(*a, **kw):
        return _FakeResponse({}, status=500)

    monkeypatch.setattr(wx.httpx, "get", fake_get)
    assert wx.get_forecast(48.1, 11.5) is None


# ─── forecast_from_settings (Config-gesteuert) ─────────────────────────────

def _patch_settings(monkeypatch, cfg):
    monkeypatch.setattr(
        wx, "load_settings",
        lambda: {"integrations": {"weather_open_meteo": cfg}},
    )


def test_forecast_from_settings_disabled_returns_none(monkeypatch):
    _patch_settings(monkeypatch, {"enabled": False, "latitude": 48.1, "longitude": 11.5})
    assert wx.forecast_from_settings() is None


def test_forecast_from_settings_no_coords_returns_none(monkeypatch):
    _patch_settings(monkeypatch, {"enabled": True, "latitude": None, "longitude": None})
    assert wx.forecast_from_settings() is None


def test_forecast_from_settings_enabled_calls_api(monkeypatch, mock_httpx_ok):
    _patch_settings(monkeypatch, {"enabled": True, "latitude": 48.1, "longitude": 11.5})
    fc = wx.forecast_from_settings(days=2)
    assert fc is not None
    assert len(fc["days"]) == 2
    assert mock_httpx_ok["params"]["latitude"] == 48.1
