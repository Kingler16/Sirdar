"""Phase-2B Tests: Readiness-Auto-Regulation (src/core/autoregulate.py) + Web.

Deckt ab:
  - adjust_workout (reine Transformation): hard-cycling→yellow, strength→yellow,
    hard→red, easy→green (unverändert), unknown→keine Änderung, Original gesichert.
  - autoregulate_day: auto wendet an + persistiert (status='adjusted', Original);
    suggest markiert nur (status='suggested', Originalplan bleibt aktiv);
    done-Tag unberührt; kein plan_day / kein readiness → graceful.
  - apply_suggestion: suggested → adjusted.
  - Dashboard rendert mit/ohne Anpassung (200).

KEINE echten config/db: temp-DB (conftest + expliziter db_path), CONFIG_DIR auf tmp.
"""

from __future__ import annotations

import json
import shutil
from datetime import date

import pytest
from starlette.testclient import TestClient

from src import config
from src.core import autoregulate as ar_mod
from src.core.autoregulate import (
    STATUS_ADJUSTED,
    STATUS_SUGGESTED,
    YELLOW_CYCLING_REDUCTION,
    adjust_workout,
    apply_suggestion,
    autoregulate_day,
)
from src.core.db import get_connection, init_db
from src.web.app import app

REAL_CONFIG_DIR = config.CONFIG_DIR


# ─── Fixtures ────────────────────────────────────────────────

@pytest.fixture()
def tmp_db(tmp_path):
    db = str(tmp_path / "autoreg.db")
    init_db(db)
    return db


def _hard_cycling() -> dict:
    return {
        "date": "2024-03-05", "sport": "cycling", "title": "VO2max",
        "type": "vo2max", "intensity": "hard", "rationale": "VO2-Block",
        "cycling": {"target": "5x4min @110% FTP", "duration_min": 80,
                    "target_tss": 100, "zones": "Z5"},
    }


def _easy_cycling() -> dict:
    return {
        "date": "2024-03-05", "sport": "cycling", "title": "Endurance",
        "intensity": "easy", "rationale": "Grundlage",
        "cycling": {"target": "Z2", "duration_min": 90, "target_tss": 60, "zones": "Z2"},
    }


def _strength() -> dict:
    return {
        "date": "2024-03-05", "sport": "strength", "title": "Unterkörper",
        "type": "lower", "intensity": "hard", "rationale": "Beinkraft",
        "strength": {"exercises": [
            {"exercise": "Kniebeuge", "sets": 4, "reps": 5, "weight_kg": 100, "rir": 2},
            {"exercise": "Beinpresse", "sets": 2, "reps": 10, "weight_kg": 160, "rir": 3},
        ]},
    }


def _rest() -> dict:
    return {"date": "2024-03-05", "sport": "rest", "title": "Ruhetag",
            "intensity": "easy", "rationale": "Erholung"}


# ════════════════════════════════════════════════════════════
#  adjust_workout — reine Transformation
# ════════════════════════════════════════════════════════════

def test_hard_cycling_yellow_softened():
    """🟡: harte Radeinheit → moderate + ~25 % weniger TSS/Dauer, Original gesichert."""
    w = _hard_cycling()
    adjusted, reason = adjust_workout(w, "yellow")
    assert adjusted is not None and reason
    assert adjusted["intensity"] == "moderate"
    factor = 1.0 - YELLOW_CYCLING_REDUCTION
    assert adjusted["cycling"]["target_tss"] == round(100 * factor)  # 75
    assert adjusted["cycling"]["duration_min"] == round(80 * factor)  # 60
    assert "abgeschwächt" in adjusted["cycling"]["target"]
    assert "abgeschwächt" in adjusted["title"]
    # Original gesichert + Eingabe nicht mutiert.
    assert adjusted["original"]["intensity"] == "hard"
    assert adjusted["original"]["cycling"]["target_tss"] == 100
    assert w["intensity"] == "hard"  # Eingabe unverändert
    assert "original" not in w


def test_strength_yellow_rir_and_set():
    """🟡: Kraft → RIR jeder Übung +1, bei >2 Sätzen ein Satz weniger."""
    w = _strength()
    adjusted, reason = adjust_workout(w, "yellow")
    assert adjusted is not None and reason
    exs = adjusted["strength"]["exercises"]
    # Kniebeuge: 4 Sätze → 3, RIR 2 → 3
    assert exs[0]["sets"] == 3
    assert exs[0]["rir"] == 3
    # Beinpresse: 2 Sätze (nicht >2) → bleibt 2, RIR 3 → 4
    assert exs[1]["sets"] == 2
    assert exs[1]["rir"] == 4
    assert adjusted["original"]["strength"]["exercises"][0]["sets"] == 4
    assert "autoreguliert" in adjusted["title"]


def test_hard_cycling_red_to_recovery():
    """🔴: harte Radeinheit → Recovery-Ausfahrt (Z1, 30–45 min), Original gesichert."""
    w = _hard_cycling()
    adjusted, reason = adjust_workout(w, "red")
    assert adjusted is not None and reason
    assert adjusted["sport"] == "cycling"
    assert adjusted["intensity"] == "easy"
    assert adjusted["cycling"]["zones"] == "Z1"
    assert 30 <= adjusted["cycling"]["duration_min"] <= 45
    assert adjusted["original"]["intensity"] == "hard"


def test_hard_strength_red_to_rest():
    """🔴: harte Krafteinheit → Ruhetag (sport=rest), strength-Details entfernt."""
    w = _strength()
    adjusted, reason = adjust_workout(w, "red")
    assert adjusted is not None and reason
    assert adjusted["sport"] == "rest"
    assert "strength" not in adjusted
    assert adjusted["original"]["sport"] == "strength"


def test_easy_green_unchanged():
    """🟢: leichte Einheit → keine Änderung."""
    adjusted, reason = adjust_workout(_easy_cycling(), "green")
    assert adjusted is None and reason is None


def test_easy_cycling_yellow_unchanged():
    """🟡: nicht-harte Radeinheit bleibt unverändert."""
    adjusted, reason = adjust_workout(_easy_cycling(), "yellow")
    assert adjusted is None and reason is None


def test_unknown_no_change():
    """❔ unknown: keine Änderung (nichts erfinden)."""
    adjusted, reason = adjust_workout(_hard_cycling(), "unknown")
    assert adjusted is None and reason is None


def test_rest_red_no_change():
    """🔴 auf bereits-Ruhetag: keine Änderung."""
    adjusted, reason = adjust_workout(_rest(), "red")
    assert adjusted is None and reason is None


def test_empty_workout_no_change():
    """Leeres/ungültiges Workout → keine Änderung (graceful)."""
    assert adjust_workout({}, "red") == (None, None)
    assert adjust_workout(None, "yellow") == (None, None)


def test_softened_cycling_yellow_again_no_change():
    """Bereits abgeschwächte (moderate) Radeinheit → 🟡 ändert nichts mehr."""
    once, _ = adjust_workout(_hard_cycling(), "yellow")  # → moderate
    twice, reason = adjust_workout(once, "yellow")
    assert twice is None and reason is None


def test_idempotent_original_preserved():
    """Doppelte Anpassung (Kraft) behält das ECHTE Original, nicht das angepasste."""
    once, _ = adjust_workout(_strength(), "yellow")
    assert once["strength"]["exercises"][0]["sets"] == 3  # 4 → 3
    twice, _ = adjust_workout(once, "yellow")
    # Original bleibt der UNVERÄNDERTE Erst-Stand (4 Sätze, RIR 2).
    assert twice["original"]["strength"]["exercises"][0]["sets"] == 4
    assert twice["original"]["strength"]["exercises"][0]["rir"] == 2
    # Die Anpassung selbst läuft weiter (3 → 2 Sätze, RIR 3 → 4).
    assert twice["strength"]["exercises"][0]["sets"] == 2
    assert twice["strength"]["exercises"][0]["rir"] == 4


# ════════════════════════════════════════════════════════════
#  DB-Helper
# ════════════════════════════════════════════════════════════

def _insert_plan_day(db, datum, workout, status="planned"):
    conn = get_connection(db)
    try:
        conn.execute(
            "INSERT INTO plan_days (datum, geplantes_workout, status) VALUES (?, ?, ?)",
            (datum, json.dumps(workout, ensure_ascii=False), status),
        )
        conn.commit()
    finally:
        conn.close()


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


def _seed_yellow_readiness(db, target_date="2024-03-05"):
    """Baseline (4 ruhige Tage) + ein gelber Tag (genau 1 Flag: wenig Schlaf)."""
    for d in ("2024-03-01", "2024-03-02", "2024-03-03", "2024-03-04"):
        _insert_health(db, d, hrv=50, rhr=50, schlaf_h=8)
    _insert_health(db, target_date, hrv=52, rhr=49, schlaf_h=5.0)  # sleep_low → 1 Flag


def _seed_red_readiness(db, target_date="2024-03-05"):
    """Baseline + ein roter Tag (≥2 Flags: HRV runter + RHR rauf)."""
    for d in ("2024-03-01", "2024-03-02", "2024-03-03", "2024-03-04"):
        _insert_health(db, d, hrv=50, rhr=50, schlaf_h=8)
    _insert_health(db, target_date, hrv=40, rhr=60, schlaf_h=8)


def _read_plan_day(db, datum):
    conn = get_connection(db)
    try:
        row = conn.execute("SELECT * FROM plan_days WHERE datum = ?", (datum,)).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    d = dict(row)
    d["workout"] = json.loads(d["geplantes_workout"]) if d["geplantes_workout"] else None
    return d


# ════════════════════════════════════════════════════════════
#  autoregulate_day — auto (apply=True)
# ════════════════════════════════════════════════════════════

def test_autoregulate_auto_applies_and_persists(tmp_db):
    """auto: gelbe Ampel + harte Radeinheit → status='adjusted', Original gesichert."""
    date_ = "2024-03-05"
    _seed_yellow_readiness(tmp_db, date_)
    _insert_plan_day(tmp_db, date_, _hard_cycling())

    res = autoregulate_day(date_, db_path=tmp_db, apply=True)
    assert res["changed"] is True
    assert res["level"] == "yellow"
    assert res["status"] == STATUS_ADJUSTED
    assert res["reason"]
    assert "sleep_low" in res["reason"]

    row = _read_plan_day(tmp_db, date_)
    assert row["status"] == STATUS_ADJUSTED
    assert row["workout"]["intensity"] == "moderate"  # hart abgeschwächt
    assert row["workout"]["original"]["intensity"] == "hard"  # Original gesichert
    assert row["anpassungsgrund"]


def test_autoregulate_auto_red_to_recovery(tmp_db):
    """auto: rote Ampel + harte Radeinheit → Recovery (Z1)."""
    date_ = "2024-03-05"
    _seed_red_readiness(tmp_db, date_)
    _insert_plan_day(tmp_db, date_, _hard_cycling())

    res = autoregulate_day(date_, db_path=tmp_db, apply=True)
    assert res["level"] == "red"
    assert res["changed"] is True
    row = _read_plan_day(tmp_db, date_)
    assert row["workout"]["cycling"]["zones"] == "Z1"


# ════════════════════════════════════════════════════════════
#  autoregulate_day — suggest (apply=False)
# ════════════════════════════════════════════════════════════

def test_autoregulate_suggest_marks_only(tmp_db):
    """suggest: status='suggested', Originalplan bleibt aktiv, Vorschlag hinterlegt."""
    date_ = "2024-03-05"
    _seed_yellow_readiness(tmp_db, date_)
    _insert_plan_day(tmp_db, date_, _hard_cycling())

    res = autoregulate_day(date_, db_path=tmp_db, apply=False)
    assert res["changed"] is True
    assert res["status"] == STATUS_SUGGESTED

    row = _read_plan_day(tmp_db, date_)
    assert row["status"] == STATUS_SUGGESTED
    # Originalplan bleibt aktiv (hart, unverändert) …
    assert row["workout"]["intensity"] == "hard"
    # … aber der Vorschlag ist hinterlegt.
    assert row["workout"]["suggestion"]["intensity"] == "moderate"


# ════════════════════════════════════════════════════════════
#  autoregulate_day — Graceful / Schutz
# ════════════════════════════════════════════════════════════

def test_autoregulate_done_untouched(tmp_db):
    """done-Tag bleibt unberührt (nie anfassen)."""
    date_ = "2024-03-05"
    _seed_red_readiness(tmp_db, date_)
    _insert_plan_day(tmp_db, date_, _hard_cycling(), status="done")

    res = autoregulate_day(date_, db_path=tmp_db, apply=True)
    assert res["changed"] is False
    row = _read_plan_day(tmp_db, date_)
    assert row["status"] == "done"
    assert row["workout"]["intensity"] == "hard"


def test_autoregulate_green_no_change(tmp_db):
    """Grüne Ampel → keine Änderung, Plan unverändert."""
    date_ = "2024-03-05"
    for d in ("2024-03-01", "2024-03-02", "2024-03-03", "2024-03-04"):
        _insert_health(tmp_db, d, hrv=50, rhr=50, schlaf_h=8)
    _insert_health(tmp_db, date_, hrv=52, rhr=49, schlaf_h=8)  # 0 Flags → green
    _insert_plan_day(tmp_db, date_, _hard_cycling())

    res = autoregulate_day(date_, db_path=tmp_db, apply=True)
    assert res["level"] == "green"
    assert res["changed"] is False
    row = _read_plan_day(tmp_db, date_)
    assert row["status"] == "planned"
    assert row["workout"]["intensity"] == "hard"


def test_autoregulate_no_plan_day_graceful(tmp_db):
    """Kein plan_day → graceful, changed=False."""
    _seed_yellow_readiness(tmp_db, "2024-03-05")
    res = autoregulate_day("2024-03-05", db_path=tmp_db, apply=True)
    assert res["changed"] is False
    assert res["before"] is None


def test_autoregulate_no_readiness_graceful(tmp_db):
    """Kein readiness (keine Health-Daten) → unknown → keine Änderung."""
    date_ = "2024-03-05"
    _insert_plan_day(tmp_db, date_, _hard_cycling())
    res = autoregulate_day(date_, db_path=tmp_db, apply=True)
    assert res["level"] == "unknown"
    assert res["changed"] is False
    row = _read_plan_day(tmp_db, date_)
    assert row["status"] == "planned"  # unverändert


def test_autoregulate_respects_settings_autonomy(tmp_db, tmp_path, monkeypatch):
    """apply=None → Autonomie aus settings (auto)."""
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    for name in ("profile.example.json", "settings.example.json"):
        shutil.copy(REAL_CONFIG_DIR / name, cfg_dir / name)
    monkeypatch.setattr(config, "CONFIG_DIR", cfg_dir)
    settings = json.loads((cfg_dir / "settings.example.json").read_text(encoding="utf-8"))
    settings["ai"]["autonomy"] = "auto"
    config.save_settings(settings)

    date_ = "2024-03-05"
    _seed_yellow_readiness(tmp_db, date_)
    _insert_plan_day(tmp_db, date_, _hard_cycling())

    res = autoregulate_day(date_, db_path=tmp_db, apply=None)
    assert res["status"] == STATUS_ADJUSTED  # autonomy=auto angewandt


# ════════════════════════════════════════════════════════════
#  apply_suggestion
# ════════════════════════════════════════════════════════════

def test_apply_suggestion_promotes(tmp_db):
    """suggested → adjusted: Vorschlag wird zum aktiven Workout."""
    date_ = "2024-03-05"
    _seed_yellow_readiness(tmp_db, date_)
    _insert_plan_day(tmp_db, date_, _hard_cycling())
    autoregulate_day(date_, db_path=tmp_db, apply=False)  # legt Vorschlag an

    res = apply_suggestion(date_, db_path=tmp_db)
    assert res["changed"] is True
    assert res["status"] == STATUS_ADJUSTED

    row = _read_plan_day(tmp_db, date_)
    assert row["status"] == STATUS_ADJUSTED
    assert row["workout"]["intensity"] == "moderate"  # Vorschlag aktiv
    assert "suggestion" not in row["workout"]


def test_apply_suggestion_no_suggestion_graceful(tmp_db):
    """Kein 'suggested'-Tag → graceful, changed=False."""
    date_ = "2024-03-05"
    _insert_plan_day(tmp_db, date_, _hard_cycling())  # status='planned'
    res = apply_suggestion(date_, db_path=tmp_db)
    assert res["changed"] is False


def test_apply_suggestion_missing_day_graceful(tmp_db):
    """Kein Plan-Tag → graceful."""
    res = apply_suggestion("2024-03-05", db_path=tmp_db)
    assert res["changed"] is False


# ════════════════════════════════════════════════════════════
#  Web — Dashboard rendert mit/ohne Anpassung
# ════════════════════════════════════════════════════════════

client = TestClient(app)


def test_dashboard_renders_without_adjustment():
    """GET / → 200, auch ohne jegliche Daten/Anpassung."""
    r = client.get("/")
    assert r.status_code == 200
    assert "Sirdar" in r.text


def test_dashboard_renders_with_adjustment(monkeypatch):
    """GET / mit heutigem suggested-Tag → 200, Auto-Regulations-Karte sichtbar."""
    from src.core import db as db_mod

    today = date.today().isoformat()
    # Auf die conftest-Session-DB (db.DB_PATH) schreiben — die Route nutzt sie.
    _seed_yellow_readiness(db_mod.DB_PATH, today)
    _insert_plan_day(db_mod.DB_PATH, today, dict(_hard_cycling(), date=today))
    # apply=None → settings (example: 'suggest') → status='suggested' beim Routenaufruf.

    r = client.get("/")
    assert r.status_code == 200
    row = _read_plan_day(db_mod.DB_PATH, today)
    assert row["status"] == STATUS_SUGGESTED  # Route hat reguliert (idempotent)
