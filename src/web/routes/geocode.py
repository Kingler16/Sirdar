"""Reverse-Geocoding für Sirdar (Settings-UX, Phase 2A+).

Endpoint:
  GET /api/geocode/reverse?lat=&lon=  -> {"name": "<Stadt>" | null, "lat": .., "lon": ..}

Hintergrund: Der Standort-Button in den Settings holt die Koordinaten per
``navigator.geolocation`` direkt im Browser. Um daraus einen menschenlesbaren
Ortsnamen anzuzeigen („Erkannt: <Stadt>"), fragen wir Nominatim (OpenStreetMap)
SERVERSEITIG ab — so vermeiden wir CORS-Probleme und können einen ``User-Agent``
setzen (Nominatim verlangt einen aussagekräftigen UA).

Designprinzip (analog Wetter/Kalender, KONZEPT §6.3): keine echten Werte erfinden,
nie crashen. Fehlt der Name (Netz-/Parse-Fehler, ungültige Koordinaten), liefert
der Endpoint ``name: null`` zurück — die JS zeigt dann nur die Koordinaten an.
"""

from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

router = APIRouter()

_NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse"
_HTTP_TIMEOUT_S = 10.0
# Nominatim-Nutzungsrichtlinie verlangt einen aussagekräftigen User-Agent.
_USER_AGENT = "Sirdar"


def _pick_place_name(address: dict) -> str | None:
    """Wählt den besten Ortsnamen aus dem Nominatim-``address``-Block.

    Bevorzugt fein → grob: city > town > village > municipality > county > state.
    """
    if not isinstance(address, dict):
        return None
    for key in ("city", "town", "village", "municipality", "county", "state"):
        value = address.get(key)
        if value:
            return str(value)
    return None


def _reverse_geocode(lat: float, lon: float) -> str | None:
    """Fragt Nominatim ab und gibt den Ortsnamen zurück (``None`` bei Fehler)."""
    params = {
        "format": "jsonv2",
        "lat": lat,
        "lon": lon,
        "zoom": 10,  # Stadt-/Ortsebene reicht.
        "addressdetails": 1,
    }
    try:
        response = httpx.get(
            _NOMINATIM_URL,
            params=params,
            headers={"User-Agent": _USER_AGENT},
            timeout=_HTTP_TIMEOUT_S,
        )
        response.raise_for_status()
        data = response.json()
    except (httpx.HTTPError, ValueError) as exc:  # ValueError: ungültiges JSON
        logger.warning("Reverse-Geocoding (Nominatim) fehlgeschlagen: %s", exc)
        return None

    try:
        name = _pick_place_name(data.get("address") or {})
        if name:
            return name
        # Fallback: gröberer display_name (erstes Segment).
        display = data.get("display_name")
        if isinstance(display, str) and display.strip():
            return display.split(",")[0].strip() or None
    except Exception:  # noqa: BLE001 — Parsing darf den Endpoint nie crashen
        logger.warning("Nominatim-Antwort nicht verwertbar.", exc_info=True)
    return None


@router.get("/api/geocode/reverse")
async def reverse_geocode(lat: float, lon: float):
    """Reverse-Geocoding: Koordinaten → Ortsname (für die Standort-Anzeige).

    Liefert IMMER 200 mit ``{"name": <str|null>, "lat": .., "lon": ..}`` —
    bei Fehlern ist ``name`` null (die JS zeigt dann nur die Koordinaten).
    """
    name = _reverse_geocode(lat, lon)
    return JSONResponse({"name": name, "lat": lat, "lon": lon})
