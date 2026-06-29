"""Trainingslast (CTL/ATL/TSB) für Sirdar — das Performance Management Chart.

Modell (Coggan/Bannister, TrainingPeaks "Performance Manager Chart"):

CTL (Chronic Training Load / "Fitness")
    Exponentiell gewichteter gleitender Mittelwert (EWMA) der täglichen TSS,
    Zeitkonstante **42 Tage**.
ATL (Acute Training Load / "Fatigue")
    Dito, Zeitkonstante **7 Tage**.
TSB (Training Stress Balance / "Form")
    TSB(heute) = CTL(gestern) − ATL(gestern).

EWMA-Form (exakte Exponential-Variante, wie GoldenCheetah):
    heute = gestern + (tss_heute − gestern) × (1 − e^(−1/N))
    mit N = 42 (CTL) bzw. 7 (ATL).

    Das ist die exakte Form. TrainingPeaks selbst nutzt häufig die lineare
    Taylor-Näherung 1. Ordnung (1/N statt 1 − e^(−1/N)); beide liefern praktisch
    identische Werte. KONZEPT §6.1 schreibt die exakte Exponential-Form vor, also
    nutzen wir die.
    Quellen (verifiziert 2026-06):
      - help.trainingpeaks.com/.../204071884-Fitness-CTL (CTL = 42-Tage-EWMA der TSS)
      - trainingpeaks.com/learn/articles/what-is-the-performance-management-chart/
        (Form = gestrige Fatigue von gestriger Fitness abgezogen)
      - paincave.io/blog/ctl-atl-tsb-explained (EWMA-Form, τ=42/7)
      - groups.google.com/g/golden-cheetah-users (exp(-1/T) vs. lineare Näherung)

Vorgehen / Designentscheidungen:
    1. Tägliche TSS aus ``workouts`` aggregieren: Summe aller ``tss`` pro
       Kalendertag. Workouts ohne TSS (z. B. GPX ohne Power, Kraft) zählen 0.
    2. Datumslücken zwischen erstem und letztem Workout-Tag mit TSS=0 auffüllen
       (Ruhetage sind reale 0-TSS-Tage — sonst wäre die EWMA-Abklingrate falsch).
    3. **Seeding ab 0**: CTL und ATL starten am ersten Tag bei 0 (dokumentiert).
       Frühe Werte sind dadurch konservativ niedrig (typischer PMC-Default;
       echte Historie würde ~6 Wochen Anlaufzeit brauchen).
    4. TSB des ersten Tages = 0 (kein "gestern" vorhanden).
    Idempotent: ``compute_load`` schreibt pro Datum genau eine Zeile (UPSERT).
"""

from __future__ import annotations

import logging
import math
from datetime import date, timedelta
from pathlib import Path

from src.core.db import get_connection, init_db

logger = logging.getLogger(__name__)

# Zeitkonstanten (Tage) der EWMA — Standard nach Coggan/TrainingPeaks.
CTL_DAYS = 42  # Chronic Training Load (Fitness)
ATL_DAYS = 7   # Acute Training Load (Fatigue)

# Abkling-/Glättungsfaktoren der exakten Exponential-EWMA: (1 − e^(−1/N)).
CTL_ALPHA = 1.0 - math.exp(-1.0 / CTL_DAYS)
ATL_ALPHA = 1.0 - math.exp(-1.0 / ATL_DAYS)

_ISO = "%Y-%m-%d"


def _daily_tss(conn) -> dict[str, float]:
    """Summiert die TSS aller Workouts je Kalendertag.

    Returns: Dict ``{"YYYY-MM-DD": tss_summe}`` (nur Tage mit ≥1 Workout).
    NULL-TSS (z. B. GPX ohne Power) zählt dank ``COALESCE`` als 0.
    """
    rows = conn.execute(
        """
        SELECT datum AS d, SUM(COALESCE(tss, 0)) AS tss_sum
        FROM workouts
        GROUP BY datum
        ORDER BY datum
        """
    ).fetchall()
    return {r["d"]: float(r["tss_sum"] or 0.0) for r in rows}


def _date_range(first: date, last: date):
    """Erzeugt jeden Kalendertag von ``first`` bis ``last`` inklusive."""
    cur = first
    while cur <= last:
        yield cur
        cur += timedelta(days=1)


def _series_from_tss(daily_tss: dict[str, float]) -> list[dict]:
    """Berechnet die CTL/ATL/TSB-Serie aus einem {datum: tss}-Dict.

    Füllt Datumslücken mit TSS=0, seedet CTL/ATL ab 0 und wendet die exakte
    EWMA an. Gibt eine chronologisch sortierte Liste von Dicts zurück
    (datum, ctl, atl, tsb), auf 2 Nachkommastellen gerundet.
    """
    if not daily_tss:
        return []

    parsed = sorted(date.fromisoformat(d) for d in daily_tss)
    first, last = parsed[0], parsed[-1]

    series: list[dict] = []
    ctl_prev = 0.0  # Seeding ab 0 (siehe Modul-Docstring).
    atl_prev = 0.0

    for i, day in enumerate(_date_range(first, last)):
        iso = day.strftime(_ISO)
        tss_today = daily_tss.get(iso, 0.0)

        # TSB nutzt die GESTRIGEN Werte → vor dem Update von ctl/atl berechnen.
        # Erster Tag: kein "gestern" → TSB = 0.
        tsb = (ctl_prev - atl_prev) if i > 0 else 0.0

        ctl = ctl_prev + (tss_today - ctl_prev) * CTL_ALPHA
        atl = atl_prev + (tss_today - atl_prev) * ATL_ALPHA

        series.append(
            {
                "datum": iso,
                "ctl": round(ctl, 2),
                "atl": round(atl, 2),
                "tsb": round(tsb, 2),
            }
        )
        ctl_prev, atl_prev = ctl, atl

    return series


def compute_load(db_path: str | Path | None = None) -> None:
    """Berechnet CTL/ATL/TSB über den gesamten Datumsbereich und schreibt ``load``.

    Liest die täglichen TSS-Summen aus ``workouts``, füllt Lücken mit 0, wendet
    die EWMA an und schreibt das Ergebnis idempotent (UPSERT pro Datum) in die
    ``load``-Tabelle. Keine Workouts → ``load`` bleibt leer (kein Fehler).
    """
    init_db(db_path)  # Schema sicherstellen (idempotent).
    conn = get_connection(db_path)
    try:
        series = _series_from_tss(_daily_tss(conn))
        if not series:
            logger.info("Keine Workouts — load-Tabelle bleibt unverändert/leer.")
            return
        conn.executemany(
            """
            INSERT INTO load (datum, ctl, atl, tsb)
            VALUES (:datum, :ctl, :atl, :tsb)
            ON CONFLICT(datum) DO UPDATE SET
                ctl = excluded.ctl,
                atl = excluded.atl,
                tsb = excluded.tsb
            """,
            series,
        )
        conn.commit()
        logger.info("load aktualisiert: %d Tage (%s … %s)",
                    len(series), series[0]["datum"], series[-1]["datum"])
    finally:
        conn.close()


def get_load_series(days: int = 90, db_path: str | Path | None = None) -> list[dict]:
    """Liefert die letzten ``days`` Einträge der ``load``-Tabelle (chronologisch).

    Args:
        days: Anzahl der jüngsten Tage (für Charts, Default 90).
        db_path: optionaler DB-Pfad (Tests).

    Returns:
        Aufsteigend nach Datum sortierte Liste von Dicts
        ``{"datum", "ctl", "atl", "tsb"}`` — leer, wenn keine Last berechnet.
    """
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        # Jüngste N Tage holen (DESC + LIMIT), dann für Charts aufsteigend drehen.
        rows = conn.execute(
            "SELECT datum, ctl, atl, tsb FROM load ORDER BY datum DESC LIMIT ?",
            (max(0, days),),
        ).fetchall()
        return [dict(r) for r in reversed(rows)]
    finally:
        conn.close()


def latest_load(db_path: str | Path | None = None) -> dict | None:
    """Gibt den jüngsten Last-Eintrag (datum, ctl, atl, tsb) zurück oder None."""
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT datum, ctl, atl, tsb FROM load ORDER BY datum DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def weekly_tss(days: int = 7, db_path: str | Path | None = None) -> float:
    """Summe der TSS der letzten ``days`` Kalendertage (relativ zum jüngsten Workout-Tag).

    Bezugspunkt ist der jüngste Tag mit einem Workout (nicht das Systemdatum),
    damit der Wert auch bei Test-/Importdaten in der Vergangenheit stimmt.
    Returns 0.0, wenn keine Workouts vorhanden sind.
    """
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        max_row = conn.execute("SELECT MAX(datum) AS m FROM workouts").fetchone()
        if not max_row or not max_row["m"]:
            return 0.0
        last = date.fromisoformat(max_row["m"])
        cutoff = (last - timedelta(days=days - 1)).strftime(_ISO)
        row = conn.execute(
            "SELECT SUM(COALESCE(tss, 0)) AS s FROM workouts WHERE datum >= ?",
            (cutoff,),
        ).fetchone()
        return round(float(row["s"] or 0.0), 1)
    finally:
        conn.close()
