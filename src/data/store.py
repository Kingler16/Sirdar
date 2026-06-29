"""Persistenz-Layer: geparste Workouts in die ``workouts``-Tabelle schreiben.

Bindeglied zwischen den Quellen-Adaptern (``src/data/sources/``) und SQLite
(``src/core/db.py``). Liest FTP aus dem Profil, parst Dateien via ``file_import``
und schreibt mit einfacher Deduplizierung in die DB.

Dedup-Schlüssel: (datum, dauer, distanz, raw_ref). Ein erneuter Import derselben
Datei (gleiches Datum/Dauer/Distanz) wird übersprungen statt dupliziert.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from src.config import load_profile
from src.core.db import get_connection, init_db
from src.data.sources.base import WorkoutDict
from src.data.sources.file_import import parse_workout_file

logger = logging.getLogger(__name__)

SUPPORTED_SUFFIXES = (".fit", ".gpx")

# Spaltenreihenfolge für den INSERT (entspricht workouts-Schema ohne id).
_COLUMNS = (
    "datum", "sportart", "typ", "dauer", "distanz",
    "np", "if_", "tss", "avg_hr", "quelle", "raw_ref", "notiz",
)


def _profile_ftp() -> int | None:
    """FTP (Watt) aus profile.json → sports.cycling.ftp_watts (oder None)."""
    profile = load_profile()
    try:
        return profile.get("sports", {}).get("cycling", {}).get("ftp_watts")
    except AttributeError:
        return None


def _is_duplicate(conn: sqlite3.Connection, w: WorkoutDict) -> bool:
    """Prüft, ob ein Workout mit gleicher (datum, dauer, distanz, raw_ref) existiert.

    NULL-sichere Vergleiche via ``IS`` (SQLite: ``a IS b`` matcht auch NULL=NULL).
    """
    row = conn.execute(
        """
        SELECT 1 FROM workouts
        WHERE datum IS ? AND dauer IS ? AND distanz IS ? AND raw_ref IS ?
        LIMIT 1
        """,
        (w.get("datum"), w.get("dauer"), w.get("distanz"), w.get("raw_ref")),
    ).fetchone()
    return row is not None


def _insert_workout(conn: sqlite3.Connection, w: WorkoutDict) -> int:
    """Fügt ein Workout ein und gibt die neue id zurück."""
    placeholders = ", ".join("?" for _ in _COLUMNS)
    cols = ", ".join(_COLUMNS)
    cur = conn.execute(
        f"INSERT INTO workouts ({cols}) VALUES ({placeholders})",
        tuple(w.get(c) for c in _COLUMNS),
    )
    return int(cur.lastrowid)


def import_file(
    path: str | Path,
    db_path: str | Path | None = None,
    ftp: int | None = None,
) -> dict:
    """Parst eine FIT/GPX-Datei und schreibt sie (dedupliziert) in die DB.

    Args:
        path: Pfad zur .fit/.gpx-Datei.
        db_path: optionaler DB-Pfad (für Tests). Default: ``db.DB_PATH``.
        ftp: optionaler FTP-Override; sonst aus dem Profil.

    Returns:
        Dict mit ``status`` ('imported' | 'skipped' | 'error'), ``raw_ref``,
        ggf. ``id`` (bei imported) und ``error`` (bei error).
    """
    path = Path(path)
    if ftp is None:
        ftp = _profile_ftp()

    try:
        workout = parse_workout_file(path, ftp=ftp)
    except Exception as exc:  # noqa: BLE001 — pro Datei robust bleiben
        logger.warning("Import fehlgeschlagen für %s: %s", path.name, exc)
        return {"status": "error", "raw_ref": path.name, "error": str(exc)}

    init_db(db_path)  # Schema sicherstellen (idempotent).
    conn = get_connection(db_path)
    try:
        if _is_duplicate(conn, workout):
            logger.info("Übersprungen (Duplikat): %s", path.name)
            return {"status": "skipped", "raw_ref": path.name}
        new_id = _insert_workout(conn, workout)
        conn.commit()
        logger.info("Importiert: %s (id=%s)", path.name, new_id)
        return {"status": "imported", "raw_ref": path.name, "id": new_id}
    finally:
        conn.close()


def import_files(
    paths: list[str | Path],
    db_path: str | Path | None = None,
    ftp: int | None = None,
) -> dict:
    """Importiert mehrere Dateien und gibt eine Zusammenfassung zurück.

    Returns:
        ``{"imported": n, "skipped": n, "errors": n, "results": [...]}``
    """
    if ftp is None:
        ftp = _profile_ftp()

    results = [import_file(p, db_path=db_path, ftp=ftp) for p in paths]
    summary = {
        "imported": sum(1 for r in results if r["status"] == "imported"),
        "skipped": sum(1 for r in results if r["status"] == "skipped"),
        "errors": sum(1 for r in results if r["status"] == "error"),
        "results": results,
    }
    return summary


def import_dir(
    directory: str | Path,
    db_path: str | Path | None = None,
    ftp: int | None = None,
) -> dict:
    """Importiert alle unterstützten Dateien (.fit/.gpx) eines Verzeichnisses."""
    directory = Path(directory)
    if not directory.is_dir():
        logger.warning("Import-Verzeichnis existiert nicht: %s", directory)
        return {"imported": 0, "skipped": 0, "errors": 0, "results": []}
    paths = sorted(
        p for p in directory.iterdir()
        if p.is_file() and p.suffix.lower() in SUPPORTED_SUFFIXES
    )
    return import_files(paths, db_path=db_path, ftp=ftp)


def recent_workouts(limit: int = 10, db_path: str | Path | None = None) -> list[dict]:
    """Gibt die letzten ``limit`` Workouts zurück (neueste zuerst).

    Sortiert nach Datum, dann id (jüngster Import zuletzt eingefügt → höchste id).
    """
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM workouts ORDER BY datum DESC, id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ─── Health-Metriken (manueller Morgen-Check, Phase 1C) ──────────────────────

# Bewertbare Health-Spalten (ohne Primary Key 'datum' und 'quelle').
_HEALTH_FIELDS = (
    "hrv", "rhr", "schlaf_h", "schlaf_qualitaet",
    "gewicht", "vo2max", "soreness", "stimmung", "readiness_score",
)


def upsert_health_metrics(
    datum: str,
    values: dict,
    quelle: str = "manual",
    db_path: str | Path | None = None,
) -> None:
    """Schreibt/aktualisiert genau eine ``health_metrics``-Zeile pro Datum (UPSERT).

    Nur in ``values`` enthaltene, nicht-``None`` Felder werden gesetzt; bestehende
    Werte anderer Felder bleiben bei einem Update erhalten (kein Überschreiben mit
    None). ``datum`` ist Primary Key → ein Eintrag pro Kalendertag.

    Args:
        datum: ISO-Datum (YYYY-MM-DD).
        values: Dict mit Teilmenge von ``_HEALTH_FIELDS``.
        quelle: Datenquelle ('manual' für den Morgen-Check).
        db_path: optionaler DB-Pfad (Tests).
    """
    init_db(db_path)
    # Nur gesetzte (nicht-None) Felder berücksichtigen.
    present = {k: values[k] for k in _HEALTH_FIELDS if values.get(k) is not None}

    cols = ["datum", *present.keys(), "quelle"]
    params = {"datum": datum, "quelle": quelle, **present}
    placeholders = ", ".join(f":{c}" for c in cols)
    # Bei Konflikt nur die übergebenen Felder + quelle aktualisieren.
    updates = ", ".join(f"{c} = excluded.{c}" for c in [*present.keys(), "quelle"])
    set_clause = f"DO UPDATE SET {updates}" if present else "DO NOTHING"

    conn = get_connection(db_path)
    try:
        conn.execute(
            f"""
            INSERT INTO health_metrics ({", ".join(cols)})
            VALUES ({placeholders})
            ON CONFLICT(datum) {set_clause}
            """,
            params,
        )
        conn.commit()
        logger.info("health_metrics upsert: %s (quelle=%s, felder=%s)",
                    datum, quelle, ", ".join(present) or "—")
    finally:
        conn.close()


def get_health_metrics(datum: str, db_path: str | Path | None = None) -> dict | None:
    """Gibt die ``health_metrics``-Zeile für ``datum`` zurück (oder None)."""
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM health_metrics WHERE datum = ?", (datum,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def health_series(
    days: int = 90,
    db_path: str | Path | None = None,
) -> list[dict]:
    """Liefert die jüngsten ``days`` Health-Tage (chronologisch aufsteigend).

    Für HRV/RHR/Schlaf-Trend-Charts. Leere Liste, wenn keine Daten vorhanden.
    """
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM health_metrics ORDER BY datum DESC LIMIT ?",
            (max(0, days),),
        ).fetchall()
        return [dict(r) for r in reversed(rows)]
    finally:
        conn.close()
