"""Web-Router für Sirdar.

Bündelt die Seiten-Router, damit ``src/web/app.py`` sie per
``app.include_router(...)`` einhängen kann.
"""

from __future__ import annotations

from src.web.routes.onboarding import router as onboarding_router

__all__ = ["onboarding_router"]
