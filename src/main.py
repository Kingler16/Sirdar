"""Sirdar CLI-Entry.

Modi:
  web      — startet das FastAPI-Dashboard (uvicorn)
  collect  — sammelt Trainings-/Health-Daten (Stub, Phase 1+)
  plan     — generiert/aktualisiert den Trainingsplan via Claude (Stub, Phase 1+)
  regulate — passt die heutige Einheit an die Tagesform an (Readiness-Ampel, Cron)

Aufruf: ``python -m src.main web`` (analog Velora src/main.py).
"""

from __future__ import annotations

import argparse
import logging
import sys

from src.config import load_settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def run_web(host: str | None = None, port: int | None = None) -> None:
    """Startet das Web-Dashboard (Uvicorn). Host/Port aus settings.web mit Override."""
    settings = load_settings()
    web_cfg = settings.get("web", {}) or {}
    host = host or web_cfg.get("host", "0.0.0.0")
    port = port or web_cfg.get("port", 8000)

    # Schema beim Start sicherstellen (idempotent), damit die DB existiert.
    from src.core.db import init_db
    init_db()

    from src.web.app import run_web_server
    logger.info("=== SIRDAR WEB START (http://%s:%s) ===", host, port)
    run_web_server(host=host, port=port)


def run_collect() -> None:
    """Datensammlung.

    Phase 1B: FIT/GPX-Import aus einem optionalen Watch-Verzeichnis (``file_import``).
    Phase 2A: Wetter-Forecast (Open-Meteo) + Kalender-Events (CalDAV/.ics), wenn die
    jeweiligen Integrationen aktiv sind. Wetter/Kalender werden — robust, nie
    crashend — abgerufen und als JSON-Cache unter ``memory/cache/`` abgelegt
    (Verzeichnis ist git-ignored).
    """
    logger.info("=== SIRDAR COLLECT ===")
    settings = load_settings()
    integrations = settings.get("integrations", {}) or {}
    fi_cfg = integrations.get("file_import", {}) or {}

    # — FIT/GPX-Import (Phase 1B) —
    if not fi_cfg.get("enabled"):
        logger.info("file_import deaktiviert (settings.integrations.file_import.enabled).")
    elif not fi_cfg.get("watch_dir"):
        logger.info("Kein watch_dir konfiguriert (settings.integrations.file_import.watch_dir) "
                    "— Datei-Import über die Web-Oberfläche (/import) nutzen.")
    else:
        from src.data.store import import_dir

        watch_dir = fi_cfg["watch_dir"]
        logger.info("Importiere FIT/GPX aus %s …", watch_dir)
        summary = import_dir(watch_dir)
        logger.info("Import fertig: %d importiert, %d übersprungen, %d Fehler.",
                    summary["imported"], summary["skipped"], summary["errors"])

    # — Wetter + Kalender (Phase 2A) —
    _collect_weather(integrations)
    _collect_calendar(integrations)

    # Health-Push, Garmin, Strava folgen in Phase 2+/3 (KONZEPT §7).


# Cache-Verzeichnis für externe Kontextdaten (git-ignored: memory/).
_CACHE_DIR = "memory/cache"


def _write_cache(name: str, data) -> None:
    """Schreibt ``data`` als JSON nach memory/cache/<name>.json (robust, nie crashend)."""
    import json
    from pathlib import Path

    try:
        cache_dir = Path(_CACHE_DIR)
        cache_dir.mkdir(parents=True, exist_ok=True)
        path = cache_dir / f"{name}.json"
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str),
                        encoding="utf-8")
        logger.info("Cache geschrieben: %s", path)
    except Exception:  # noqa: BLE001 — Caching ist optional, nie blockierend
        logger.warning("Konnte %s-Cache nicht schreiben.", name, exc_info=True)


def _collect_weather(integrations: dict) -> None:
    """Ruft den Open-Meteo-Forecast ab (falls aktiv) und cached ihn."""
    cfg = integrations.get("weather_open_meteo", {}) or {}
    if not cfg.get("enabled"):
        logger.info("weather_open_meteo deaktiviert — kein Wetter-Forecast.")
        return
    from src.data.sources.weather_open_meteo import forecast_from_settings

    logger.info("Rufe Open-Meteo-Forecast ab …")
    forecast = forecast_from_settings(days=14)
    if not forecast:
        logger.info("Kein Wetter-Forecast erhalten (keine Koordinaten oder Netzfehler).")
        return
    logger.info("Wetter-Forecast erhalten: %d Tage.", len(forecast.get("days", [])))
    _write_cache("weather", forecast)


def _collect_calendar(integrations: dict) -> None:
    """Ruft Kalender-Events ab (falls aktiv) und cached sie."""
    cfg = integrations.get("calendar_caldav", {}) or {}
    if not cfg.get("enabled"):
        logger.info("calendar_caldav deaktiviert — keine Kalender-Events.")
        return
    from src.data.sources.calendar_caldav import events_from_settings

    logger.info("Rufe Kalender-Events ab …")
    events = events_from_settings(days=28)
    logger.info("Kalender-Events erhalten: %d.", len(events))
    if events:
        _write_cache("calendar", events)


def run_plan() -> None:
    """Stub — KI-Plan-Generierung via Claude folgt in Phase 1+."""
    logger.info("=== SIRDAR PLAN (Stub) ===")
    logger.info("Noch nicht implementiert. Phase 1+: Kontext-JSON → ask_claude("
                "Coach-System-Prompt) → plan_days.")


def run_regulate() -> None:
    """Tägliche Readiness-Auto-Regulation (KONZEPT §6.2) — für den Morgen-Cron.

    Deterministisch & regelbasiert (kein Claude-Aufruf): passt die HEUTIGE geplante
    Einheit an die Tagesform-Ampel an. Bei autonomy='auto' wird die Anpassung direkt
    angewandt (status='adjusted'), bei 'suggest' nur vorgeschlagen (status='suggested',
    Bestätigung in der Web-App). Idempotent — gefahrlos täglich ausführbar.
    """
    logger.info("=== SIRDAR REGULATE (Readiness-Auto-Regulation) ===")

    from src.core.autoregulate import autoregulate_day

    result = autoregulate_day()  # heutiger Tag, Autonomie aus settings
    level = result.get("level")
    if not result.get("changed"):
        logger.info("Tag %s, Ampel '%s': keine Anpassung nötig (Plan unverändert).",
                    result.get("date"), level)
        return
    logger.info("Tag %s, Ampel '%s': %s — %s",
                result.get("date"), level, result.get("status"), result.get("reason"))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Sirdar — KI-Trainings-Coach")
    parser.add_argument(
        "mode",
        choices=["web", "collect", "plan", "regulate"],
        help="Modus: web (Dashboard), collect (Daten sammeln, Stub), plan (KI-Plan, Stub), "
             "regulate (Readiness-Auto-Regulation, Cron)",
    )
    parser.add_argument("--host", help="Host für den Web-Modus (Override settings.web.host)")
    parser.add_argument("--port", "-p", type=int, help="Port für den Web-Modus (Override settings.web.port)")
    args = parser.parse_args(argv)

    if args.mode == "web":
        run_web(host=args.host, port=args.port)
    elif args.mode == "collect":
        run_collect()
    elif args.mode == "plan":
        run_plan()
    elif args.mode == "regulate":
        run_regulate()


if __name__ == "__main__":
    sys.exit(main())
