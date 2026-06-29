"""Manuelle Health-Eingabe für Sirdar (Phase 1C, „Morgen-Check").

Seiten:
  GET  /health/log  -> Morgen-Check-Formular (datum default heute, vorbefüllt,
                       falls für das Datum schon Werte existieren)
  POST /health/log  -> Upsert in health_metrics (ein Eintrag/Datum, quelle='manual')

Bis zur Wearable-Integration (Phase 3) trägt der User HRV, Ruhepuls, Schlaf,
Schlafqualität, Soreness, Stimmung und Gewicht hier von Hand ein. Diese Werte
speisen die Readiness-Ampel (src/core/readiness.py).

Persistenz via ``store.upsert_health_metrics`` (UPSERT pro Datum). Leere Felder
werden als None gespeichert (kein Überschreiben bestehender Werte mit None).
Stil folgt src/web/routes/onboarding.py (_to_int/_to_float, RedirectResponse).
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from src.data.store import get_health_metrics, upsert_health_metrics
from src.web.deps import ctx, templates

router = APIRouter()


# ─── Hilfsfunktionen: Form-Werte -> typsicher (analog onboarding.py) ─────────

def _to_int(value: str | None) -> int | None:
    if value is None:
        return None
    value = value.strip()
    if value == "":
        return None
    try:
        return int(float(value))
    except (ValueError, TypeError):
        return None


def _to_float(value: str | None) -> float | None:
    if value is None:
        return None
    value = value.strip()
    if value == "":
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def _today_iso() -> str:
    return date.today().isoformat()


@router.get("/health/log", response_class=HTMLResponse)
async def health_log_page(request: Request, datum: str | None = None, saved: bool = False):
    """Zeigt den Morgen-Check, vorbefüllt aus vorhandenen Werten für das Datum."""
    day = datum or _today_iso()
    existing = get_health_metrics(day) or {}
    return templates.TemplateResponse(
        request, "health_log.html",
        ctx(request, "health", day=day, existing=existing, saved=saved),
    )


@router.post("/health/log")
async def health_log_save(request: Request):
    """Nimmt die Morgen-Check-Werte und schreibt sie (UPSERT) in health_metrics."""
    form = await request.form()
    day = (form.get("datum") or "").strip() or _today_iso()

    values = {
        "hrv": _to_float(form.get("hrv")),
        "rhr": _to_int(form.get("rhr")),
        "schlaf_h": _to_float(form.get("schlaf_h")),
        "schlaf_qualitaet": _to_int(form.get("schlaf_qualitaet")),
        "soreness": _to_int(form.get("soreness")),
        "stimmung": _to_int(form.get("stimmung")),
        "gewicht": _to_float(form.get("gewicht")),
    }
    upsert_health_metrics(day, values, quelle="manual")
    return RedirectResponse(f"/health/log?datum={day}&saved=1", status_code=303)
