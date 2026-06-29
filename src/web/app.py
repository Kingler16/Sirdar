"""FastAPI-Dashboard für Sirdar.

Routen:
  GET /                      -> Dashboard (Health-Overview-Platzhalter + Claude-Status)
  /plan /profile /goals /settings -> Onboarding-/Eingabe-Seiten (src/web/routes/)
  GET /manifest.webmanifest  -> PWA-Manifest
  GET /sw.js                 -> Service Worker (Root-Scope)
  /static/...                -> CSS/Icons/JS (HTMX)

Sprache (?lang=de|en) wird per Cookie gemerkt; Default aus settings.locale.
Aufbau folgt Velora (src/web/app.py, src/web/routes/pwa.py).
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from src.core.claude import claude_available
from src.core.load import compute_load, get_load_series, latest_load, weekly_tss
from src.core.readiness import readiness_for_date
from src.data.store import health_series, recent_workouts
from src.web.deps import STATIC_DIR, ctx, resolve_lang, templates
from src.web.i18n import SUPPORTED_LANGS
from src.web.routes import coach_router, health_router, imports_router, onboarding_router

logger = logging.getLogger(__name__)

app = FastAPI(title="Sirdar")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
app.include_router(onboarding_router)
app.include_router(imports_router)
app.include_router(health_router)
app.include_router(coach_router)

# Anzahl Tage für Fitness/Form- und Trend-Charts.
_CHART_DAYS = 90
# Anzahl Workouts in der Dashboard-Tabelle.
_RECENT_WORKOUTS = 5


# ─── HTML Pages ──────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    """Health-Overview-Dashboard: Readiness, Fitness/Form, Wochen-Load, Trends.

    ``compute_load()`` wird beim Laden aufgerufen, damit die ``load``-Tabelle
    stets den aktuellen Workout-Stand widerspiegelt (idempotent, billig).
    """
    compute_load()  # load-Tabelle aktualisieren (idempotent).

    load_series = get_load_series(days=_CHART_DAYS)
    trend_series = health_series(days=_CHART_DAYS)
    readiness = readiness_for_date()
    has_trends = any(
        r.get("hrv") is not None or r.get("rhr") is not None or r.get("schlaf_h") is not None
        for r in trend_series
    )

    return templates.TemplateResponse(
        request, "dashboard.html",
        ctx(
            request, "dashboard",
            claude_ok=claude_available(),
            readiness=readiness,
            load_series=load_series,
            latest=latest_load(),
            week_load=weekly_tss(days=7),
            trend_series=trend_series,
            has_trends=has_trends,
            workouts=recent_workouts(limit=_RECENT_WORKOUTS),
        ),
    )


@app.get("/set-lang/{lang}")
async def set_lang(lang: str, request: Request):
    """Sprach-Umschalter: setzt Cookie und leitet zurück (Referer oder /)."""
    if lang not in SUPPORTED_LANGS:
        lang = "de"
    target = request.headers.get("referer") or "/"
    resp = RedirectResponse(target, status_code=303)
    resp.set_cookie("sirdar_lang", lang, max_age=60 * 60 * 24 * 365, httponly=True, samesite="lax")
    return resp


@app.get("/api/status")
async def api_status():
    """Maschinenlesbarer System-Status (für spätere HTMX-Partials/Health-Check)."""
    return JSONResponse({"app": "Sirdar", "claude_cli": claude_available()})


# ─── PWA: Manifest + Service Worker (Root-Scope) ─────────────

def _public_base(request: Request) -> str:
    """Öffentliche Basis-URL aus dem Request (settings.web.public_url später)."""
    return str(request.base_url).rstrip("/")


@app.get("/manifest.webmanifest", include_in_schema=False)
async def manifest(request: Request):
    base = _public_base(request)
    payload = {
        "name": "Sirdar",
        "short_name": "Sirdar",
        "description": "Self-hosted AI training coach — cycling & strength.",
        "start_url": f"{base}/",
        "scope": f"{base}/",
        "display": "standalone",
        "orientation": "portrait",
        "theme_color": "#0d0f12",
        "background_color": "#0d0f12",
        "lang": resolve_lang(request),
        "dir": "ltr",
        "categories": ["health", "fitness", "sports"],
        "icons": [
            {"src": "/static/icon.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "any maskable"},
        ],
    }
    return JSONResponse(payload, media_type="application/manifest+json")


@app.get("/sw.js", include_in_schema=False)
async def service_worker():
    return FileResponse(
        STATIC_DIR / "sw.js",
        media_type="application/javascript",
        headers={
            "Service-Worker-Allowed": "/",
            "Cache-Control": "no-cache, no-store, must-revalidate",
        },
    )


def run_web_server(host: str = "0.0.0.0", port: int = 8000) -> None:
    """Startet den Uvicorn-Server."""
    import uvicorn

    uvicorn.run(app, host=host, port=port, workers=1, log_level="info")
