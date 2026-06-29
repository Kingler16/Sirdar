"""Sirdar CLI-Entry.

Modi:
  web      — startet das FastAPI-Dashboard (uvicorn)
  collect  — sammelt Trainings-/Health-Daten (Stub, Phase 1+)
  plan     — generiert/aktualisiert den Trainingsplan via Claude (Stub, Phase 1+)

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
    """Stub — Datensammlung (FIT/GPX, Health, Wetter, Kalender) folgt in Phase 1+."""
    logger.info("=== SIRDAR COLLECT (Stub) ===")
    logger.info("Noch nicht implementiert. Phase 1+: FIT/GPX-Import, Health-Push, "
                "Open-Meteo, CalDAV → SQLite.")


def run_plan() -> None:
    """Stub — KI-Plan-Generierung via Claude folgt in Phase 1+."""
    logger.info("=== SIRDAR PLAN (Stub) ===")
    logger.info("Noch nicht implementiert. Phase 1+: Kontext-JSON → ask_claude("
                "Coach-System-Prompt) → plan_days.")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Sirdar — KI-Trainings-Coach")
    parser.add_argument(
        "mode",
        choices=["web", "collect", "plan"],
        help="Modus: web (Dashboard), collect (Daten sammeln, Stub), plan (KI-Plan, Stub)",
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


if __name__ == "__main__":
    sys.exit(main())
