"""Zentraler Config-Loader für Sirdar.

Lädt ``config/profile.json`` und ``config/settings.json``. Existiert die echte
Datei nicht (z.B. frische Checkout / vor dem Onboarding), wird transparent auf
die mitgelieferte ``*.example.json`` zurückgefallen, damit die App auch ohne
User-Setup startet. ENV-Variablen überlagern Secrets aus settings.json — wichtig
für Production (RockPi via systemd EnvironmentFile), damit Tokens nicht zwingend
auf der Platte liegen müssen (Velora-Pattern, src/config_loader.py).

Schreiben passiert ausschließlich über ``atomic_write_json`` (tmp + os.replace),
damit ein abgebrochener Write nie eine halbe/korrupte JSON-Datei hinterlässt.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# config/ liegt parallel zu src/
CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"

# (section, key) -> ENV-Variable. ENV gewinnt immer über den Datei-Wert.
# Sektion None bedeutet: Top-Level-Key in settings.json.
_ENV_OVERRIDES: dict[tuple[str | None, str], str] = {
    ("claude", "oauth_token"): "CLAUDE_CODE_OAUTH_TOKEN",
    (None, "locale"): "SIRDAR_LOCALE",
}


def _load_json(path: Path) -> dict:
    """Liest eine JSON-Datei; gibt {} zurück bei Fehler/Nichtexistenz."""
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        logger.warning("Konnte %s nicht lesen — nutze leeres Dict", path, exc_info=True)
    return {}


def _load_with_example_fallback(name: str) -> dict:
    """Lädt config/<name>.json, fällt auf config/<name>.example.json zurück.

    name: 'profile' oder 'settings' (ohne Endung).
    """
    real = CONFIG_DIR / f"{name}.json"
    example = CONFIG_DIR / f"{name}.example.json"
    if real.exists():
        return _load_json(real)
    logger.info("%s.json nicht vorhanden — nutze %s.example.json als Fallback", name, name)
    return _load_json(example)


def load_settings() -> dict:
    """Liest settings.json (oder settings.example.json) + ENV-Overrides."""
    data = _load_with_example_fallback("settings")
    for (section, key), env_name in _ENV_OVERRIDES.items():
        val = os.getenv(env_name)
        if not val:
            continue
        if section is None:
            data[key] = val
        else:
            data.setdefault(section, {})[key] = val
    return data


def load_profile() -> dict:
    """Liest profile.json (oder profile.example.json als Fallback)."""
    return _load_with_example_fallback("profile")


def get_locale() -> str:
    """Aktuelle UI-Sprache aus settings.locale (Default: 'de')."""
    return load_settings().get("locale", "de")


def atomic_write_json(path: Path, data: dict) -> None:
    """Schreibt ``data`` atomar als JSON nach ``path`` (tmp-Datei + os.replace).

    Verhindert korrupte/halbe Dateien bei Absturz oder paralleler Lese-Operation
    (Velora-Memory-Pattern). Legt fehlende Parent-Verzeichnisse an.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False, default=str)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def save_settings(data: dict) -> None:
    """Speichert settings.json atomar (schreibt NIE die *.example.json)."""
    atomic_write_json(CONFIG_DIR / "settings.json", data)


def save_profile(data: dict) -> None:
    """Speichert profile.json atomar (schreibt NIE die *.example.json)."""
    atomic_write_json(CONFIG_DIR / "profile.json", data)
