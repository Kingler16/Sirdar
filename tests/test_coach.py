"""Phase-1D Tests: Coach-Brain (src/core/coach.py) + Plan-/Coach-Web-Routen.

KEINE echten Claude-Aufrufe: ``src.core.claude.ask_claude`` wird gemonkeypatcht und
liefert eine kanonische Antwort mit genau einem ```json-Plan-Block (das ist die
Schnittstelle, die coach.py konsumiert — ``{"text","structured"}``).

DB: die session-weite Umleitung aus conftest (temp training.db) greift; alle
coach-Funktionen bekommen zusätzlich explizit ``db_path=tmp`` für klare Isolation.
config: CONFIG_DIR wird auf ein tmp-Verzeichnis umgebogen (kein echtes profile.json).
"""

from __future__ import annotations

import json
import shutil
from datetime import date, timedelta

import pytest
from starlette.testclient import TestClient

from src import config
from src.core import coach as coach_mod
from src.core.claude import ClaudeCLIError
from src.core.coach import (
    CoachError,
    build_context,
    coach_reply,
    generate_plan,
    get_chat_history,
    get_plan_days,
)
from src.web.app import app

REAL_CONFIG_DIR = config.CONFIG_DIR


# ─── Fixtures ────────────────────────────────────────────────

@pytest.fixture()
def tmp_db(tmp_path):
    """Frische temp-DB pro Test (Plan/Chat-Isolation)."""
    return str(tmp_path / "coach.db")


@pytest.fixture()
def seeded_profile(tmp_path, monkeypatch):
    """Schreibt ein vollständiges Profil (FTP, Übungen, Ziel) in ein tmp-CONFIG_DIR."""
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    for name in ("profile.example.json", "settings.example.json"):
        shutil.copy(REAL_CONFIG_DIR / name, cfg_dir / name)
    monkeypatch.setattr(config, "CONFIG_DIR", cfg_dir)

    profile = {
        "name": "Max",
        "weight_kg": 76.0,
        "sports": {
            "cycling": {"enabled": True, "ftp_watts": 250, "experience": "advanced"},
            "strength": {
                "enabled": True, "experience": "intermediate",
                "current_state": {
                    "frequency_per_week": 2,
                    "current_exercises": [
                        {"exercise": "Kniebeuge", "sets": 4, "reps": 5, "weight_kg": 100},
                    ],
                },
            },
        },
        "goals": [
            {"id": "g1", "type": "ftp_increase", "sport": "cycling",
             "target": "280", "priority": "primary", "horizon_weeks": 12},
        ],
    }
    config.save_profile(profile)
    return cfg_dir


def _future(n: int) -> str:
    return (date.today() + timedelta(days=n)).isoformat()


def _canonical_plan_text(days=None, summary="Sweet-Spot-Block für FTP-Aufbau"):
    """Kanonische Coach-Antwort: Prosa + genau ein ```json-Plan-Block."""
    if days is None:
        days = [
            {"date": date.today().isoformat(), "sport": "cycling",
             "title": "Sweet Spot", "type": "sweet_spot", "intensity": "moderate",
             "rationale": "FTP-Aufbau bei grüner Readiness",
             "cycling": {"target": "3x15min @90% FTP", "duration_min": 75,
                         "target_tss": 85, "zones": "Z4"}},
            {"date": _future(1), "sport": "strength",
             "title": "Unterkörper", "type": "lower", "intensity": "hard",
             "rationale": "Beinkraft mit Abstand zum harten Radtag",
             "strength": {"exercises": [
                 {"exercise": "Kniebeuge", "sets": 4, "reps": 5, "weight_kg": 102.5, "rir": 2}]}},
            {"date": _future(2), "sport": "rest", "title": "Ruhetag",
             "intensity": "easy", "rationale": "Erholung schützen"},
        ]
    payload = {
        "summary": summary,
        "block": {"phase": "build", "week_type": "load", "horizon": "week"},
        "days": days,
    }
    prosa = ("Wir starten einen Build-Block mit Sweet-Spot-Fokus, weil dein Primärziel "
             "ein FTP-Anstieg ist und die Zeit begrenzt scheint.")
    return prosa + "\n\n```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```"


@pytest.fixture()
def mock_claude(monkeypatch):
    """Patcht ask_claude in coach + claude-Modul auf eine kanonische Plan-Antwort.

    Liefert einen Recorder, dessen ``.text`` man umsetzen kann, um andere Antworten
    zu simulieren (leer, ungültig, reiner Text ohne Plan).
    """
    from src.core import claude as claude_mod
    from src.core.claude import extract_json_block

    class Recorder:
        text = _canonical_plan_text()
        calls: list = []

    def fake_ask(system_prompt, user_prompt, *a, **kw):
        Recorder.calls.append({"system": system_prompt, "user": user_prompt})
        return {"text": Recorder.text, "structured": extract_json_block(Recorder.text)}

    monkeypatch.setattr(coach_mod, "ask_claude", fake_ask)
    monkeypatch.setattr(claude_mod, "ask_claude", fake_ask)
    return Recorder


# ════════════════════════════════════════════════════════════
#  build_context
# ════════════════════════════════════════════════════════════

def test_build_context_fields(seeded_profile, tmp_db):
    ctx = build_context(db_path=tmp_db)
    assert ctx["today"] == date.today().isoformat()
    assert "cycling" in ctx["sports"]
    assert ctx["sports"]["cycling"]["ftp_watts"] == 250
    assert ctx["goals"][0]["id"] == "g1"  # example-Ziel rausgefiltert
    assert ctx["settings"]["tone"] == "balanced"
    assert ctx["settings"]["planning_horizon"] == "week"
    # leere DB → load None, weekly_tss 0, readiness unknown — kein Crash.
    assert ctx["load"] is None
    assert ctx["weekly_tss"] == 0.0
    assert ctx["readiness"]["level"] == "unknown"
    assert ctx["data_gaps"] == []  # vollständiges Profil → keine Lücken


def test_build_context_detects_gaps(tmp_path, monkeypatch, tmp_db):
    """Leeres Profil (Fallback example) → Lücken werden markiert, kein Crash."""
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    for name in ("profile.example.json", "settings.example.json"):
        shutil.copy(REAL_CONFIG_DIR / name, cfg_dir / name)
    monkeypatch.setattr(config, "CONFIG_DIR", cfg_dir)

    ctx = build_context(db_path=tmp_db)
    assert "no_goals" in ctx["data_gaps"]
    assert "cycling_no_ftp" in ctx["data_gaps"]
    assert "strength_no_current_exercises" in ctx["data_gaps"]


# ════════════════════════════════════════════════════════════
#  generate_plan
# ════════════════════════════════════════════════════════════

def test_generate_plan_writes_plan_days(seeded_profile, mock_claude, tmp_db):
    result = generate_plan(horizon="week", db_path=tmp_db)
    assert result["summary"]
    assert result["rationale"]  # Prosa ohne json-Block
    assert "```json" not in result["rationale"]
    assert len(result["days"]) == 3

    rows = get_plan_days(days=14, db_path=tmp_db)
    by_date = {r["datum"]: r for r in rows}
    today = date.today().isoformat()
    assert today in by_date
    assert by_date[today]["status"] == "planned"
    assert by_date[today]["phase"] == "build"
    assert by_date[today]["woche_typ"] == "load"
    # präskriptive Felder im geparsten workout-JSON
    w = by_date[today]["workout"]
    assert w["sport"] == "cycling"
    assert w["cycling"]["duration_min"] == 75
    # Kraft-Tag
    w2 = by_date[_future(1)]["workout"]
    assert w2["strength"]["exercises"][0]["weight_kg"] == 102.5


def test_generate_plan_only_future(seeded_profile, mock_claude, tmp_db):
    """Vergangene Tage aus der KI-Antwort werden NICHT geschrieben."""
    past = (date.today() - timedelta(days=3)).isoformat()
    mock_claude.text = _canonical_plan_text(days=[
        {"date": past, "sport": "cycling", "title": "alt", "intensity": "easy",
         "rationale": "vergangen", "cycling": {"target": "x"}},
        {"date": date.today().isoformat(), "sport": "rest", "title": "heute",
         "intensity": "easy", "rationale": "heute"},
    ])
    result = generate_plan(horizon="week", db_path=tmp_db)
    dates = [d["date"] for d in result["days"]]
    assert past not in dates
    assert date.today().isoformat() in dates
    assert get_plan_days(from_date=past, days=2, db_path=tmp_db) == [] or \
        all(r["datum"] >= date.today().isoformat() for r in get_plan_days(days=14, db_path=tmp_db))


def test_generate_plan_does_not_overwrite_done(seeded_profile, mock_claude, tmp_db):
    """Ein als 'done' markierter Tag bleibt bei Neuplanung unangetastet."""
    from src.core.db import get_connection, init_db
    init_db(tmp_db)
    today = date.today().isoformat()
    conn = get_connection(tmp_db)
    conn.execute(
        "INSERT INTO plan_days (datum, geplantes_workout, status) VALUES (?, ?, 'done')",
        (today, json.dumps({"sport": "cycling", "title": "erledigt"})),
    )
    conn.commit()
    conn.close()

    generate_plan(horizon="week", db_path=tmp_db)
    rows = {r["datum"]: r for r in get_plan_days(days=14, db_path=tmp_db)}
    assert rows[today]["status"] == "done"  # nicht überschrieben
    assert rows[today]["workout"]["title"] == "erledigt"


def test_generate_plan_upsert(seeded_profile, mock_claude, tmp_db):
    """Zweite Generierung fürs gleiche Datum aktualisiert (kein Duplikat)."""
    generate_plan(horizon="week", db_path=tmp_db)
    generate_plan(horizon="week", db_path=tmp_db)
    rows = get_plan_days(days=14, db_path=tmp_db)
    dates = [r["datum"] for r in rows]
    assert len(dates) == len(set(dates))  # jedes Datum genau einmal


def test_generate_plan_horizon_limits_days(seeded_profile, mock_claude, tmp_db):
    """Horizont 'day' begrenzt die Zahl persistierter Tage auf max. 2."""
    many = [
        {"date": _future(i), "sport": "rest", "title": f"d{i}",
         "intensity": "easy", "rationale": "r"}
        for i in range(0, 10)
    ]
    mock_claude.text = _canonical_plan_text(days=many)
    result = generate_plan(horizon="day", db_path=tmp_db)
    assert len(result["days"]) == 2  # _HORIZON_DAYS['day'] == 2


def test_generate_plan_empty_answer_raises(seeded_profile, mock_claude, tmp_db):
    """Antwort ohne json-Block → CoachError (kein Crash, sauber)."""
    mock_claude.text = "Nur Prosa, kein Plan."
    with pytest.raises(CoachError):
        generate_plan(horizon="week", db_path=tmp_db)


def test_generate_plan_invalid_json_raises(seeded_profile, mock_claude, tmp_db):
    """Plan-JSON ohne 'days'-Array → CoachError."""
    mock_claude.text = "Text\n\n```json\n{\"summary\": \"x\"}\n```"
    with pytest.raises(CoachError):
        generate_plan(horizon="week", db_path=tmp_db)


def test_generate_plan_propagates_cli_error(seeded_profile, monkeypatch, tmp_db):
    """ClaudeCLIError (z. B. CLI fehlt) wird sauber durchgereicht."""
    def boom(*a, **kw):
        raise ClaudeCLIError("Claude CLI nicht installiert")
    monkeypatch.setattr(coach_mod, "ask_claude", boom)
    with pytest.raises(ClaudeCLIError):
        generate_plan(horizon="week", db_path=tmp_db)


def test_generate_plan_empty_days_ok(seeded_profile, mock_claude, tmp_db):
    """Leeres days-Array (Daten-Integrität) ist KEIN Fehler — sauber leer zurück."""
    mock_claude.text = _canonical_plan_text(days=[])
    result = generate_plan(horizon="week", db_path=tmp_db)
    assert result["days"] == []
    assert get_plan_days(days=14, db_path=tmp_db) == []


# ════════════════════════════════════════════════════════════
#  coach_reply (Chat)
# ════════════════════════════════════════════════════════════

def test_coach_reply_persists_chat_and_plan(seeded_profile, mock_claude, tmp_db):
    result = coach_reply("Plan die nächste Woche", history=[], db_path=tmp_db)
    assert result["plan_updated"] is True
    assert result["reply"]
    assert "```json" not in result["reply"]

    chat = get_chat_history(db_path=tmp_db)
    assert len(chat) == 2
    assert chat[0]["rolle"] == "user"
    assert chat[0]["inhalt"] == "Plan die nächste Woche"
    assert chat[1]["rolle"] == "assistant"
    # Plan wurde geschrieben
    assert len(get_plan_days(days=14, db_path=tmp_db)) >= 1


def test_coach_reply_text_only_no_plan(seeded_profile, mock_claude, tmp_db):
    """Reine Erklärung ohne json-Block → Chat gespeichert, kein Plan-Update."""
    mock_claude.text = "Sweet Spot liegt bei 88–94 % deiner FTP."
    result = coach_reply("Was ist Sweet Spot?", history=[], db_path=tmp_db)
    assert result["plan_updated"] is False
    assert result["reply"].startswith("Sweet Spot")
    assert get_plan_days(days=14, db_path=tmp_db) == []
    assert len(get_chat_history(db_path=tmp_db)) == 2


def test_coach_reply_empty_message_raises(seeded_profile, mock_claude, tmp_db):
    with pytest.raises(CoachError):
        coach_reply("   ", history=[], db_path=tmp_db)


def test_coach_reply_no_chat_on_cli_error(seeded_profile, monkeypatch, tmp_db):
    """Bei CLI-Fehler wird NICHTS in chat geschrieben (sauberer Zustand)."""
    def boom(*a, **kw):
        raise ClaudeCLIError("Timeout")
    monkeypatch.setattr(coach_mod, "ask_claude", boom)
    with pytest.raises(ClaudeCLIError):
        coach_reply("Hallo", history=[], db_path=tmp_db)
    assert get_chat_history(db_path=tmp_db) == []


# ════════════════════════════════════════════════════════════
#  Web-Routen
# ════════════════════════════════════════════════════════════

client = TestClient(app)


def test_plan_page_renders_empty():
    """GET /plan → 200, auch ohne Plan (Empty-State)."""
    r = client.get("/plan")
    assert r.status_code == 200
    assert "Sirdar" in r.text


def test_plan_generate_route(seeded_profile, mock_claude, monkeypatch):
    """POST /plan/generate rendert das Plan-Partial (200) mit Coach-Antwort.

    claude_available wird True gemockt, damit die Coach-Steuerung aktiv ist;
    der DB-Pfad ist die conftest-temp-DB.
    """
    monkeypatch.setattr("src.web.routes.coach.claude_available", lambda: True)
    r = client.post("/plan/generate", data={"horizon": "week"})
    assert r.status_code == 200
    assert 'id="plan-body"' in r.text


def test_settings_post_persists_horizon_and_tone(tmp_path, monkeypatch):
    """POST /settings speichert planning_horizon + coach_tone in settings.json."""
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    for name in ("profile.example.json", "settings.example.json"):
        shutil.copy(REAL_CONFIG_DIR / name, cfg_dir / name)
    monkeypatch.setattr(config, "CONFIG_DIR", cfg_dir)

    r = client.post(
        "/settings",
        data={"locale": "de", "autonomy": "auto",
              "planning_horizon": "block", "coach_tone": "balanced"},
        follow_redirects=False,
    )
    assert r.status_code == 303
    saved = json.loads((cfg_dir / "settings.json").read_text(encoding="utf-8"))
    assert saved["ai"]["planning_horizon"] == "block"
    assert saved["ai"]["coach_tone"] == "balanced"
