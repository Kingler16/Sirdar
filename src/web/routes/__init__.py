"""Web-Router für Sirdar.

Bündelt die Seiten-Router, damit ``src/web/app.py`` sie per
``app.include_router(...)`` einhängen kann.
"""

from __future__ import annotations

from src.web.routes.coach import router as coach_router
from src.web.routes.health import router as health_router
from src.web.routes.imports import router as imports_router
from src.web.routes.onboarding import router as onboarding_router

__all__ = ["coach_router", "health_router", "imports_router", "onboarding_router"]
