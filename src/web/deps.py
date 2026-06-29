"""Geteilte Web-Helfer für Sirdar (Templates, Sprache, Template-Kontext).

Wird sowohl von ``src/web/app.py`` als auch von den Routern unter
``src/web/routes/`` genutzt, damit Sprachauflösung und Jinja2-Instanz an einer
Stelle leben (kein Duplizieren von ``_resolve_lang``/``_ctx`` pro Modul).
"""

from __future__ import annotations

from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates

from src.config import get_locale
from src.web.i18n import SUPPORTED_LANGS, get_translations

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

# Eine gemeinsame Jinja2-Instanz für alle Routen.
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def resolve_lang(request: Request) -> str:
    """Sprache bestimmen: Cookie > settings.locale > 'de'."""
    cookie = request.cookies.get("sirdar_lang")
    if cookie in SUPPORTED_LANGS:
        return cookie
    locale = get_locale()
    return locale if locale in SUPPORTED_LANGS else "de"


def ctx(request: Request, page: str, **extra) -> dict:
    """Baut den Template-Kontext mit Sprache + Übersetzungen.

    ``page`` markiert den aktiven Bottom-Nav-Eintrag (base.html).
    """
    lang = resolve_lang(request)
    base = {"request": request, "page": page, "lang": lang, "t": get_translations(lang)}
    base.update(extra)
    return base
