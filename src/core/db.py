"""SQLite-Layer für Sirdar (training.db).

Connection-Helper + ``init_db()``, das das Schema aus KONZEPT §5 anlegt:
workouts, strength_logs, health_metrics, load, plan_days, chat.

Zeitreihen (Workouts, Health, Plan, Chat) liegen in SQLite; menschenlesbare
Config (Profil, Ziele, Settings) liegt als JSON in config/ (siehe src/config.py).
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)

# training.db liegt im Projekt-Root (parallel zu src/). Per .gitignore (*.db).
DB_PATH = Path(__file__).resolve().parent.parent.parent / "training.db"


def get_connection(db_path: Path | str | None = None) -> sqlite3.Connection:
    """Öffnet eine SQLite-Connection mit sinnvollen Defaults.

    - ``row_factory = sqlite3.Row``: Zugriff per Spaltenname.
    - WAL-Journal + Foreign-Keys aktiviert (Concurrency + Integrität).
    Caller ist für ``close()`` verantwortlich (oder nutzt den Context-Manager).
    """
    path = Path(db_path) if db_path else DB_PATH
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


# Schema nach KONZEPT §5. CREATE TABLE IF NOT EXISTS → idempotent.
_SCHEMA = """
-- Aktivitäten (Rad, Lauf, Kraft-Session, ...)
CREATE TABLE IF NOT EXISTS workouts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    datum       TEXT NOT NULL,              -- ISO-Datum (YYYY-MM-DD)
    sportart    TEXT NOT NULL,              -- cycling | strength | running | ...
    typ         TEXT,                       -- z.B. sweet_spot, vo2max, upper_lower
    dauer       REAL,                       -- Minuten
    distanz     REAL,                       -- km
    np          REAL,                       -- Normalized Power (Watt)
    if_         REAL,                       -- Intensity Factor (Spalte: 'if' ist SQL-Keyword)
    tss         REAL,                       -- Training Stress Score
    avg_hr      INTEGER,                    -- Ø-Herzfrequenz
    quelle      TEXT,                       -- file_import | garmin | strava | manual
    raw_ref     TEXT,                       -- Referenz auf Rohdatei (FIT/GPX)
    notiz       TEXT
);

-- Kraft-Detail je Satz (gehört zu einem workout mit sportart='strength')
CREATE TABLE IF NOT EXISTS strength_logs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    workout_id  INTEGER NOT NULL REFERENCES workouts(id) ON DELETE CASCADE,
    uebung      TEXT NOT NULL,              -- Übung
    satz        INTEGER,                    -- Satz-Nummer
    wdh         INTEGER,                    -- Wiederholungen
    gewicht     REAL,                       -- kg
    rir         INTEGER                     -- Reps in Reserve
);

-- Tägliche Gesundheits-/Readiness-Metriken
CREATE TABLE IF NOT EXISTS health_metrics (
    datum            TEXT PRIMARY KEY,      -- ISO-Datum, ein Eintrag pro Tag
    hrv              REAL,
    rhr              INTEGER,               -- Ruhepuls
    schlaf_h         REAL,                  -- Schlafstunden
    schlaf_qualitaet INTEGER,              -- subjektiv 1–5 / 0–100
    gewicht          REAL,                  -- kg
    vo2max           REAL,
    soreness         INTEGER,               -- subjektiv
    stimmung         INTEGER,               -- subjektiv
    readiness_score  REAL,                  -- abgeleitete Ampel 0–100
    quelle           TEXT                   -- apple_health | garmin | manual
);

-- Abgeleitete Trainingslast (CTL/ATL/TSB)
CREATE TABLE IF NOT EXISTS load (
    datum   TEXT PRIMARY KEY,              -- ISO-Datum
    ctl     REAL,                          -- Chronic Training Load (Fitness)
    atl     REAL,                          -- Acute Training Load (Fatigue)
    tsb     REAL                           -- Training Stress Balance (Form = ctl-atl)
);

-- Geplante Trainingstage (vom KI-Coach erzeugt/angepasst)
CREATE TABLE IF NOT EXISTS plan_days (
    datum            TEXT PRIMARY KEY,      -- ISO-Datum
    geplantes_workout TEXT,                 -- JSON (präskriptive Vorgaben)
    status           TEXT,                  -- planned | done | skipped | adjusted
    phase            TEXT,                  -- base | build | peak | taper
    woche_typ        TEXT,                  -- load | recovery (3:1-Rhythmus)
    anpassungsgrund  TEXT                   -- "Warum"-Begründung der KI
);

-- Coach-Chat-Verlauf (für Streaming-Chat, --session-id)
CREATE TABLE IF NOT EXISTS chat (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         TEXT NOT NULL,               -- ISO-Timestamp
    rolle      TEXT NOT NULL,               -- user | assistant
    inhalt     TEXT NOT NULL,
    session_id TEXT                         -- Claude-CLI Session-ID
);

CREATE INDEX IF NOT EXISTS idx_workouts_datum    ON workouts(datum);
CREATE INDEX IF NOT EXISTS idx_workouts_sportart ON workouts(sportart);
CREATE INDEX IF NOT EXISTS idx_strength_workout  ON strength_logs(workout_id);
CREATE INDEX IF NOT EXISTS idx_chat_session      ON chat(session_id);
"""

# Erwartete Tabellen — für init_db()-Verifikation und Tests.
EXPECTED_TABLES = (
    "workouts",
    "strength_logs",
    "health_metrics",
    "load",
    "plan_days",
    "chat",
)


def init_db(db_path: Path | str | None = None) -> Path:
    """Legt das vollständige Schema an (idempotent) und gibt den DB-Pfad zurück."""
    path = Path(db_path) if db_path else DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = get_connection(path)
    try:
        conn.executescript(_SCHEMA)
        conn.commit()
    finally:
        conn.close()
    logger.info("SQLite-Schema initialisiert: %s", path)
    return path


def list_tables(db_path: Path | str | None = None) -> list[str]:
    """Gibt die Namen aller User-Tabellen zurück (für Verifikation/Tests)."""
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        return [r["name"] for r in rows]
    finally:
        conn.close()
