"""Phase-1C Tests: Readiness-Ampel (regelbasiert, KONZEPT §6.2/§6.3).

Deckt green/yellow/red/unknown ab + Baseline-Bau + DB-Helper.
"""

import pytest

from src.core.db import get_connection, init_db
from src.core.readiness import (
    HRV_LOW_FACTOR,
    MIN_BASELINE_DAYS,
    RHR_HIGH_DELTA,
    SLEEP_LOW_HOURS,
    build_baseline,
    compute_readiness,
    readiness_for_date,
)


# Eine gesunde Baseline mit ausreichend Datentagen.
GOOD_BASELINE = {"hrv": 50.0, "rhr": 50, "n_days": 7}


# ─── compute_readiness: Kern-Ampellogik ──────────────────────

def test_green_no_flags():
    """Alle Werte im Normbereich → 0 Flags → green."""
    day = {"hrv": 52, "rhr": 49, "schlaf_h": 8.0, "soreness": 1, "stimmung": 4}
    res = compute_readiness(day, GOOD_BASELINE)
    assert res["level"] == "green"
    assert res["flags"] == []
    assert res["score"] == 90


def test_yellow_one_flag():
    """Genau eine Auffälligkeit (zu wenig Schlaf) → 1 Flag → yellow."""
    day = {"hrv": 52, "rhr": 49, "schlaf_h": 5.0, "soreness": 1, "stimmung": 4}
    res = compute_readiness(day, GOOD_BASELINE)
    assert res["level"] == "yellow"
    assert res["flags"] == ["sleep_low"]


def test_red_two_flags():
    """Zwei Auffälligkeiten (HRV niedrig + RHR hoch) → ≥2 Flags → red."""
    day = {"hrv": 40, "rhr": 60, "schlaf_h": 8.0, "soreness": 1, "stimmung": 4}
    res = compute_readiness(day, GOOD_BASELINE)
    assert res["level"] == "red"
    assert set(res["flags"]) == {"hrv_low", "rhr_high"}


def test_red_three_flags():
    """Drei Auffälligkeiten → red."""
    day = {"hrv": 40, "rhr": 60, "schlaf_h": 5.0, "soreness": 5, "stimmung": 1}
    res = compute_readiness(day, GOOD_BASELINE)
    assert res["level"] == "red"
    assert len(res["flags"]) >= 2


def test_unknown_thin_baseline():
    """Baseline mit < MIN_BASELINE_DAYS Datentagen → unknown."""
    day = {"hrv": 40, "rhr": 60, "schlaf_h": 5.0}
    thin = {"hrv": 50.0, "rhr": 50, "n_days": MIN_BASELINE_DAYS - 1}
    res = compute_readiness(day, thin)
    assert res["level"] == "unknown"
    assert res["score"] is None


def test_unknown_no_metrics_today():
    """Genug Baseline, aber heute keine bewertbare Metrik → unknown (nichts erfinden)."""
    res = compute_readiness({}, GOOD_BASELINE)
    assert res["level"] == "unknown"


def test_missing_single_metric_skipped():
    """Fehlt HRV heute, ist aber Schlaf ok → keine Flags → green (HRV übersprungen)."""
    day = {"rhr": 49, "schlaf_h": 8.0}
    res = compute_readiness(day, GOOD_BASELINE)
    assert res["level"] == "green"


# ─── Threshold-Grenzen ───────────────────────────────────────

def test_hrv_threshold_boundary():
    """HRV genau auf der Schwelle (Baseline × Faktor) ist NICHT unter der Schwelle."""
    base = {"hrv": 50.0, "rhr": 50, "n_days": 7}
    on_threshold = 50.0 * HRV_LOW_FACTOR
    res = compute_readiness({"hrv": on_threshold}, base)
    assert res["flags"] == []  # < strikt, also kein Flag bei Gleichheit


def test_rhr_threshold_boundary():
    """RHR genau bei Baseline+DELTA ist NICHT erhöht (Vergleich ist strikt >)."""
    base = {"hrv": 50.0, "rhr": 50, "n_days": 7}
    res = compute_readiness({"rhr": 50 + RHR_HIGH_DELTA}, base)
    assert res["flags"] == []


def test_sleep_threshold_boundary():
    """Schlaf genau bei SLEEP_LOW_HOURS ist NICHT zu wenig (Vergleich ist strikt <)."""
    res = compute_readiness({"schlaf_h": SLEEP_LOW_HOURS}, GOOD_BASELINE)
    assert res["flags"] == []


# ─── build_baseline ──────────────────────────────────────────

def test_build_baseline_averages_and_counts():
    """build_baseline mittelt HRV/RHR und zählt die Datentage."""
    rows = [
        {"hrv": 50, "rhr": 48},
        {"hrv": 54, "rhr": 52},
        {"hrv": None, "rhr": 50},  # HRV fehlt → bei HRV-Mittel ausgelassen
    ]
    base = build_baseline(rows)
    assert base["n_days"] == 3
    assert base["hrv"] == pytest.approx((50 + 54) / 2)
    assert base["rhr"] == pytest.approx((48 + 52 + 50) / 3)


def test_build_baseline_window_limit():
    """Es werden höchstens window_days Tage berücksichtigt."""
    rows = [{"hrv": 50, "rhr": 50} for _ in range(20)]
    base = build_baseline(rows, window_days=7)
    assert base["n_days"] == 7


# ─── readiness_for_date: DB-Helper ───────────────────────────

@pytest.fixture()
def temp_db(tmp_path):
    db = tmp_path / "readiness_test.db"
    init_db(db)
    return db


def _insert_health(db, datum, **kw):
    cols = ["datum", *kw.keys(), "quelle"]
    vals = [datum, *kw.values(), "manual"]
    placeholders = ", ".join("?" for _ in cols)
    conn = get_connection(db)
    try:
        conn.execute(
            f"INSERT INTO health_metrics ({', '.join(cols)}) VALUES ({placeholders})",
            vals,
        )
        conn.commit()
    finally:
        conn.close()


def test_readiness_for_date_no_data(temp_db):
    """Keine Health-Daten → unknown, datum None."""
    res = readiness_for_date(db_path=temp_db)
    assert res["level"] == "unknown"
    assert res["datum"] is None


def test_readiness_for_date_with_baseline(temp_db):
    """Genug Vortage als Baseline + auffälliger heutiger Tag → red."""
    # 4 ruhige Tage als Baseline (HRV~50, RHR~50).
    for d in ("2024-03-01", "2024-03-02", "2024-03-03", "2024-03-04"):
        _insert_health(temp_db, d, hrv=50, rhr=50, schlaf_h=8)
    # Heute deutlich schlechter: HRV runter, RHR rauf.
    _insert_health(temp_db, "2024-03-05", hrv=40, rhr=60, schlaf_h=8)

    res = readiness_for_date("2024-03-05", db_path=temp_db)
    assert res["level"] == "red"
    assert res["datum"] == "2024-03-05"
    assert res["metrics"]["hrv"] == 40


def test_readiness_for_date_defaults_to_latest(temp_db):
    """Ohne Datum wird der jüngste Health-Tag bewertet."""
    for d in ("2024-03-01", "2024-03-02", "2024-03-03"):
        _insert_health(temp_db, d, hrv=50, rhr=50, schlaf_h=8)
    _insert_health(temp_db, "2024-03-10", hrv=52, rhr=49, schlaf_h=8)
    res = readiness_for_date(db_path=temp_db)
    assert res["datum"] == "2024-03-10"
    assert res["level"] == "green"
