"""FastAPI-Dashboard für Sirdar (Phase 0).

Routen:
  GET /                      -> Dashboard (Health-Overview-Platzhalter + Claude-Status)
  GET /manifest.webmanifest  -> PWA-Manifest
  GET /sw.js                 -> Service Worker (Root-Scope)
  /static/...                -> CSS/Icons

Sprache (?lang=de|en) wird per Cookie gemerkt; Default aus settings.locale.
Aufbau folgt Velora (src/web/app.py, src/web/routes/pwa.py).
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from src.config import get_locale
from src.core.claude import claude_available
from src.web.i18n import SUPPORTED_LANGS, get_translations

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

app = FastAPI(title="Sirdar")
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def _resolve_lang(request: Request) -> str:
    """Sprache bestimmen: Cookie > settings.locale > 'de'."""
    cookie = request.cookies.get("sirdar_lang")
    if cookie in SUPPORTED_LANGS:
        return cookie
    locale = get_locale()
    return locale if locale in SUPPORTED_LANGS else "de"


def _ctx(request: Request, page: str, **extra) -> dict:
    """Baut den Template-Kontext mit Übersetzungen."""
    lang = _resolve_lang(request)
    ctx = {"request": request, "page": page, "lang": lang, "t": get_translations(lang)}
    ctx.update(extra)
    return ctx


# ─── HTML Pages ──────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    """Dashboard mit Health-Overview-Platzhalter + Claude-CLI-Status."""
    return templates.TemplateResponse(
        request, "dashboard.html",
        _ctx(request, "dashboard", claude_ok=claude_available()),
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
        "lang": _resolve_lang(request),
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
