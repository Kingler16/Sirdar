"""Internationalisierung für das Sirdar-Dashboard (DE/EN).

Minimaler dict-basierter Mechanismus (Velora-Pattern, src/web/i18n.py).
Default-Sprache kommt aus settings.locale (siehe src/config.get_locale).

Usage:
    from src.web.i18n import get_translations
    t = get_translations("de")
    t["nav"]["dashboard"]  -> "Übersicht"
"""

from __future__ import annotations

_TRANSLATIONS: dict[str, dict] = {
    # ── Deutsch (Default) ─────────────────────────────────────────────
    "de": {
        "app_name": "Sirdar",
        "tagline": "Dein KI-Trainings-Coach",
        "nav": {
            "dashboard": "Übersicht",
            "plan": "Plan",
            "health": "Gesundheit",
            "goals": "Ziele",
            "chat": "Coach",
            "settings": "Einstellungen",
        },
        "common": {
            "loading": "Lädt…",
            "language": "Sprache",
            "coming_soon": "kommt bald",
        },
        "dashboard": {
            "title": "Übersicht",
            "subtitle": "Deine Gesundheit & dein Training auf einen Blick",
            "health_overview": "Health Overview",
            "health_overview_soon": "Health Overview kommt bald.",
            "health_overview_body": "Readiness-Ampel, HRV/RHR/Schlaf-Trends und "
                                    "CTL/ATL/TSB erscheinen hier, sobald Daten erfasst sind.",
            "system_status": "System-Status",
            "claude_status": "Claude CLI",
            "claude_ok": "erreichbar",
            "claude_missing": "nicht gefunden",
            "claude_hint": "Installiere die Claude Code CLI und hinterlege einen "
                           "OAuth-Token (claude setup-token), damit der Coach Pläne erstellen kann.",
        },
    },
    # ── English ───────────────────────────────────────────────────────
    "en": {
        "app_name": "Sirdar",
        "tagline": "Your AI training coach",
        "nav": {
            "dashboard": "Overview",
            "plan": "Plan",
            "health": "Health",
            "goals": "Goals",
            "chat": "Coach",
            "settings": "Settings",
        },
        "common": {
            "loading": "Loading…",
            "language": "Language",
            "coming_soon": "coming soon",
        },
        "dashboard": {
            "title": "Overview",
            "subtitle": "Your health & training at a glance",
            "health_overview": "Health Overview",
            "health_overview_soon": "Health Overview coming soon.",
            "health_overview_body": "Readiness traffic light, HRV/RHR/sleep trends and "
                                    "CTL/ATL/TSB will appear here once data is collected.",
            "system_status": "System Status",
            "claude_status": "Claude CLI",
            "claude_ok": "reachable",
            "claude_missing": "not found",
            "claude_hint": "Install the Claude Code CLI and set an OAuth token "
                           "(claude setup-token) so the coach can build plans.",
        },
    },
}

SUPPORTED_LANGS = tuple(_TRANSLATIONS.keys())


def get_translations(lang: str = "de") -> dict:
    """Gibt das Übersetzungs-Dict für ``lang`` zurück (Fallback: Deutsch).

    >>> get_translations("en")["nav"]["dashboard"]
    'Overview'
    >>> get_translations("xx")["app_name"]
    'Sirdar'
    """
    return _TRANSLATIONS.get(lang, _TRANSLATIONS["de"])
