"""Phase-1C Tests: Trainingslast (CTL/ATL/TSB) — EWMA-Mathematik + Persistenz.

Verifiziert die exakte Exponential-EWMA gegen bekannte Eigenschaften:
  - konstante 100 TSS/Tag über lange Zeit → CTL→100, ATL→100, TSB→0,
  - Einzel-Spike (ATL reagiert schneller als CTL),
  - Datumslücken werden mit TSS=0 aufgefüllt,
  - Idempotenz (zweimal compute_load → keine Duplikate, gleiche Werte),
  - keine Workouts → leere Serie.

Alle DB-Tests nutzen einen temp-DB-Pfad (db_path-Param) — nie die echte training.db.
"""

import math

import pytest

from src.core.db import get_connection, init_db
from src.core.load import (
    ATL_ALPHA,
    ATL_DAYS,
    CTL_ALPHA,
    CTL_DAYS,
    _series_from_tss,
    compute_load,
    get_load_series,
    latest_load,
    weekly_tss,
)


@pytest.fixture()
def temp_db(tmp_path):
    db = tmp_path / "load_test.db"
    init_db(db)
    return db


def _insert_workout(db, datum, tss, sportart="cycling"):
    conn = get_connection(db)
    try:
        conn.execute(
            "INSERT INTO workouts (datum, sportart, tss, quelle) VALUES (?, ?, ?, 'test')",
            (datum, sportart, tss),
        )
        conn.commit()
    finally:
        conn.close()


# ─── Reine EWMA-Mathematik (ohne DB) ─────────────────────────

def test_alpha_constants():
    """Glättungsfaktoren = exakte Exponential-Form (1 − e^(−1/N))."""
    assert CTL_DAYS == 42 and ATL_DAYS == 7
    assert CTL_ALPHA == pytest.approx(1.0 - math.exp(-1.0 / 42))
    assert ATL_ALPHA == pytest.approx(1.0 - math.exp(-1.0 / 7))


def test_constant_tss_converges_to_100():
    """Konstante 100 TSS/Tag über lange Zeit → CTL→100, ATL→100, TSB→0."""
    days = 400
    daily = {f"2025-{(i // 28) % 12 + 1:02d}-01": 0 for i in range(0)}  # leer init
    # Saubere fortlaufende Datumsreihe bauen.
    from datetime import date, timedelta
    start = date(2024, 1, 1)
    daily = {(start + timedelta(days=i)).isoformat(): 100.0 for i in range(days)}

    series = _series_from_tss(daily)
    last = series[-1]
    assert last["ctl"] == pytest.approx(100.0, abs=0.1)
    assert last["atl"] == pytest.approx(100.0, abs=0.01)
    assert last["tsb"] == pytest.approx(0.0, abs=0.2)


def test_first_day_seeding_from_zero():
    """Seeding ab 0: Tag 1 mit 100 TSS → CTL/ATL = 100·alpha, TSB = 0."""
    series = _series_from_tss({"2024-01-01": 100.0})
    assert len(series) == 1
    d0 = series[0]
    assert d0["ctl"] == pytest.approx(round(100.0 * CTL_ALPHA, 2))
    assert d0["atl"] == pytest.approx(round(100.0 * ATL_ALPHA, 2))
    assert d0["tsb"] == 0.0  # erster Tag: kein "gestern"


def test_tsb_uses_yesterday_values():
    """TSB(heute) = CTL(gestern) − ATL(gestern)."""
    series = _series_from_tss({"2024-01-01": 100.0, "2024-01-02": 100.0})
    d0, d1 = series[0], series[1]
    assert d1["tsb"] == pytest.approx(round(d0["ctl"] - d0["atl"], 2))


def test_atl_reacts_faster_than_ctl_on_spike():
    """Einzel-Spike am letzten Tag: ATL steigt stärker als CTL (kürzere Zeitkonstante)."""
    daily = {"2024-01-01": 0.0, "2024-01-02": 0.0, "2024-01-03": 200.0}
    series = _series_from_tss(daily)
    last = series[-1]
    assert last["atl"] > last["ctl"]  # 7d reagiert schneller als 42d
    # TSB des Spike-Tags nutzt die (nahe 0) Vortageswerte → ~0.
    assert last["tsb"] == pytest.approx(0.0, abs=0.01)


def test_empty_series():
    """Keine TSS → leere Serie."""
    assert _series_from_tss({}) == []


# ─── Datumslücken ────────────────────────────────────────────

def test_date_gaps_filled_with_zero():
    """Lücke zwischen zwei Workout-Tagen wird mit 0-TSS-Tagen aufgefüllt."""
    # Workouts nur am 1. und 5. → Serie muss alle 5 Kalendertage enthalten.
    daily = {"2024-01-01": 100.0, "2024-01-05": 100.0}
    series = _series_from_tss(daily)
    dates = [d["datum"] for d in series]
    assert dates == [
        "2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05",
    ]
    # An den Lückentagen klingt die Last ab (TSS=0) → ATL fällt von Tag 1 zu Tag 4.
    atl_day1 = series[0]["atl"]
    atl_day4 = series[3]["atl"]
    assert atl_day4 < atl_day1


# ─── DB-Integration: compute_load / get_load_series ──────────

def test_compute_load_writes_rows(temp_db):
    """compute_load schreibt einen load-Eintrag pro Kalendertag."""
    _insert_workout(temp_db, "2024-03-01", 100.0)
    _insert_workout(temp_db, "2024-03-03", 50.0)  # Lücke am 02.
    compute_load(db_path=temp_db)

    series = get_load_series(days=90, db_path=temp_db)
    assert [d["datum"] for d in series] == ["2024-03-01", "2024-03-02", "2024-03-03"]
    assert all("ctl" in d and "atl" in d and "tsb" in d for d in series)


def test_compute_load_aggregates_same_day(temp_db):
    """Mehrere Workouts am selben Tag → TSS summiert sich."""
    _insert_workout(temp_db, "2024-03-01", 60.0)
    _insert_workout(temp_db, "2024-03-01", 40.0)  # Summe 100
    compute_load(db_path=temp_db)
    d0 = get_load_series(db_path=temp_db)[0]
    assert d0["ctl"] == pytest.approx(round(100.0 * CTL_ALPHA, 2))


def test_compute_load_idempotent(temp_db):
    """Zweimaliges compute_load → keine Duplikate, identische Werte."""
    _insert_workout(temp_db, "2024-03-01", 100.0)
    _insert_workout(temp_db, "2024-03-02", 80.0)
    compute_load(db_path=temp_db)
    first = get_load_series(db_path=temp_db)
    compute_load(db_path=temp_db)
    second = get_load_series(db_path=temp_db)
    assert first == second
    assert len(second) == 2  # genau 2 Tage, keine Duplikate


def test_null_tss_counts_as_zero(temp_db):
    """Workouts ohne TSS (z. B. GPX ohne Power) zählen als 0 TSS."""
    _insert_workout(temp_db, "2024-03-01", None)  # kein TSS
    compute_load(db_path=temp_db)
    d0 = get_load_series(db_path=temp_db)[0]
    assert d0["ctl"] == 0.0
    assert d0["atl"] == 0.0


def test_no_workouts_empty_load(temp_db):
    """Keine Workouts → load bleibt leer, kein Fehler."""
    compute_load(db_path=temp_db)
    assert get_load_series(db_path=temp_db) == []
    assert latest_load(db_path=temp_db) is None


def test_weekly_tss(temp_db):
    """weekly_tss summiert die TSS der letzten 7 Tage (relativ zum jüngsten Workout)."""
    _insert_workout(temp_db, "2024-03-01", 50.0)   # 8 Tage vor dem jüngsten → raus
    _insert_workout(temp_db, "2024-03-05", 100.0)  # innerhalb 7d
    _insert_workout(temp_db, "2024-03-08", 70.0)   # jüngster Tag
    assert weekly_tss(days=7, db_path=temp_db) == pytest.approx(170.0)


def test_weekly_tss_no_workouts(temp_db):
    """weekly_tss ohne Workouts → 0.0."""
    assert weekly_tss(db_path=temp_db) == 0.0
