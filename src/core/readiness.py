"""Readiness-Ampel für Sirdar — regelbasierte tägliche Auto-Regulation.

Umsetzung von KONZEPT §6.2 (Readiness-Ampel) + §6.3 (keine Werte erfinden):
Aus HRV-Trend (vs. individueller Baseline), Ruhepuls (RHR), Schlaf, subjektivem
Check (Stimmung) und Soreness wird eine Ampel abgeleitet:

  🟢 green   — Plan wie vorgesehen.
  🟡 yellow  — harte Einheit abschwächen.
  🔴 red     — harte Einheit → Recovery/Ruhe.
  ❔ unknown — zu wenig Daten für eine seriöse Aussage (nichts erfinden).

Logik: Jede auffällige Metrik erzeugt ein "Flag". Anzahl der Flags → Ampel:
    0 Flags  → green
    1 Flag   → yellow
    ≥2 Flags → red

Baseline:
    Rollierender Mittelwert der letzten ~7 Tage (vor dem betrachteten Tag) je
    Metrik. Stehen weniger als ``MIN_BASELINE_DAYS`` (=3) Datentage zur Verfügung,
    ist keine seriöse Baseline möglich → Ampel ``unknown`` (graceful, KONZEPT §6.3).

Designentscheidung: Es werden nur Metriken bewertet, die heute UND als Baseline
vorhanden sind. Fehlende einzelne Metriken werden übersprungen (kein Flag, keine
erfundenen Werte). Fehlt heute jede bewertbare Metrik → ``unknown``.

Thresholds: bewusst konservativ und als benannte Konstanten gehalten (siehe unten),
abgeleitet aus gängiger Praxis (HRV-/RHR-Trend-Auswertung, z. B. HRV4Training /
TrainingPeaks-Readiness). Wo wir unsicher sind, eher reduzieren als forcieren.
"""

from __future__ import annotations

import logging
from statistics import mean

logger = logging.getLogger(__name__)

# ── Thresholds (benannte Konstanten) ──────────────────────────────────────
# HRV gilt als unterdrückt, wenn sie unter Baseline × Faktor fällt (≈ −10 %).
HRV_LOW_FACTOR = 0.90
# RHR gilt als erhöht, wenn sie mehr als +N Schläge über Baseline liegt.
RHR_HIGH_DELTA = 5
# Schlaf gilt als zu kurz unterhalb dieser Stundenzahl (absoluter Schwellwert).
SLEEP_LOW_HOURS = 6.0
# Soreness (subjektiv, Skala wie erfasst): ab diesem Wert "hoch".
SORENESS_HIGH = 4
# Stimmung (subjektiv): bis zu diesem Wert "niedrig".
MOOD_LOW = 2

# Mindestanzahl Datentage in der Baseline, sonst -> unknown.
MIN_BASELINE_DAYS = 3
# Fenster (Tage) für die rollierende Baseline.
BASELINE_WINDOW_DAYS = 7

# Metriken, die für Soreness/Stimmung-Schwellen relevant sind (subjektiv).
_LEVELS = {0: "green", 1: "yellow"}  # ab 2 -> red (siehe _level_from_flags)


def _level_from_flags(n_flags: int) -> str:
    """0 → green, 1 → yellow, ≥2 → red."""
    return _LEVELS.get(n_flags, "red")


def _to_score(level: str) -> int | None:
    """Grobe 0–100-Kennzahl für die ``readiness_score``-Spalte (optional)."""
    return {"green": 90, "yellow": 60, "red": 30}.get(level)


def compute_readiness(day_metrics: dict | None, baseline: dict | None) -> dict:
    """Bewertet die Tagesbereitschaft aus heutigen Metriken + Baseline.

    Args:
        day_metrics: Heutige Werte, z. B.
            ``{"hrv": 48, "rhr": 52, "schlaf_h": 7.5, "soreness": 2, "stimmung": 4}``.
            Einzelne Schlüssel dürfen fehlen/None sein.
        baseline: Erwartet
            ``{"hrv": <Mittel>, "rhr": <Mittel>, "n_days": <int>}`` —
            der rollierende 7-Tage-Mittelwert je Metrik plus die Anzahl der
            tatsächlich vorhandenen Datentage (``n_days``).

    Returns:
        ``{"level": "green"|"yellow"|"red"|"unknown", "score": int|None,
           "flags": [str, ...]}``.
        ``unknown`` (score None), wenn Baseline zu dünn ist (< MIN_BASELINE_DAYS)
        oder heute keine bewertbare Metrik vorliegt.
    """
    day_metrics = day_metrics or {}
    baseline = baseline or {}

    n_days = int(baseline.get("n_days") or 0)
    if n_days < MIN_BASELINE_DAYS:
        return {"level": "unknown", "score": None, "flags": []}

    flags: list[str] = []
    evaluated = 0  # wie viele Metriken konnten wir überhaupt bewerten?

    hrv = day_metrics.get("hrv")
    hrv_base = baseline.get("hrv")
    if hrv is not None and hrv_base:
        evaluated += 1
        if hrv < hrv_base * HRV_LOW_FACTOR:
            flags.append("hrv_low")

    rhr = day_metrics.get("rhr")
    rhr_base = baseline.get("rhr")
    if rhr is not None and rhr_base:
        evaluated += 1
        if rhr > rhr_base + RHR_HIGH_DELTA:
            flags.append("rhr_high")

    sleep = day_metrics.get("schlaf_h")
    if sleep is not None:
        evaluated += 1
        if sleep < SLEEP_LOW_HOURS:
            flags.append("sleep_low")

    soreness = day_metrics.get("soreness")
    if soreness is not None:
        evaluated += 1
        if soreness >= SORENESS_HIGH:
            flags.append("soreness_high")

    mood = day_metrics.get("stimmung")
    if mood is not None:
        evaluated += 1
        if mood <= MOOD_LOW:
            flags.append("mood_low")

    # Keine einzige Metrik bewertbar → nichts erfinden.
    if evaluated == 0:
        return {"level": "unknown", "score": None, "flags": []}

    level = _level_from_flags(len(flags))
    return {"level": level, "score": _to_score(level), "flags": flags}


def _avg(values: list[float | int | None]) -> float | None:
    """Mittelwert über die Nicht-None-Werte; None, wenn keine vorhanden."""
    clean = [v for v in values if v is not None]
    return mean(clean) if clean else None


def build_baseline(
    history_rows: list[dict],
    window_days: int = BASELINE_WINDOW_DAYS,
) -> dict:
    """Baut die rollierende Baseline aus den letzten ``window_days`` Health-Tagen.

    Args:
        history_rows: Liste von health_metrics-Dicts der Tage VOR heute,
            beliebig sortiert; es werden bis zu ``window_days`` davon genutzt.
            (Der Aufrufer übergibt die jüngsten zuerst oder zuletzt — wir nehmen
            schlicht die übergebenen Zeilen, daher sollte er sie bereits auf das
            Fenster begrenzt haben; siehe ``readiness_for_date``.)

    Returns:
        ``{"hrv": <Mittel|None>, "rhr": <Mittel|None>, "n_days": <int>}``.
        ``n_days`` = Anzahl der berücksichtigten Tage (für die unknown-Schwelle).
    """
    rows = list(history_rows)[:window_days]
    return {
        "hrv": _avg([r.get("hrv") for r in rows]),
        "rhr": _avg([r.get("rhr") for r in rows]),
        "n_days": len(rows),
    }


def readiness_for_date(
    target_date: str | None = None,
    db_path=None,
) -> dict:
    """Helper: zieht heutige Metrik + Baseline aus ``health_metrics`` und bewertet.

    Args:
        target_date: ISO-Datum (YYYY-MM-DD) des zu bewertenden Tages. Default:
            der jüngste Tag mit Health-Daten (nicht zwingend "heute" — robust
            gegen Lücken und Test-/Importdaten in der Vergangenheit).
        db_path: optionaler DB-Pfad (Tests).

    Returns:
        Das ``compute_readiness``-Dict, zusätzlich angereichert um ``"datum"``
        (der bewertete Tag) und ``"metrics"`` (die heutigen Rohwerte) für die UI.
        Gibt ``level="unknown"`` zurück, wenn gar keine Health-Daten existieren.
    """
    # Lazy import vermeidet eine harte Kopplung von readiness an db beim Testen
    # der reinen Bewertungslogik (compute_readiness braucht keine DB).
    from src.core.db import get_connection, init_db

    init_db(db_path)
    conn = get_connection(db_path)
    try:
        if target_date is None:
            row = conn.execute(
                "SELECT MAX(datum) AS m FROM health_metrics"
            ).fetchone()
            target_date = row["m"] if row else None
            if not target_date:
                return {"level": "unknown", "score": None, "flags": [],
                        "datum": None, "metrics": None}

        today_row = conn.execute(
            "SELECT * FROM health_metrics WHERE datum = ?", (target_date,)
        ).fetchone()
        day_metrics = dict(today_row) if today_row else {}

        # Baseline: bis zu BASELINE_WINDOW_DAYS Tage VOR dem Zieldatum, jüngste zuerst.
        hist_rows = conn.execute(
            """
            SELECT * FROM health_metrics
            WHERE datum < ?
            ORDER BY datum DESC
            LIMIT ?
            """,
            (target_date, BASELINE_WINDOW_DAYS),
        ).fetchall()
        baseline = build_baseline([dict(r) for r in hist_rows])

        result = compute_readiness(day_metrics, baseline)
        result["datum"] = target_date
        result["metrics"] = day_metrics or None
        return result
    finally:
        conn.close()
