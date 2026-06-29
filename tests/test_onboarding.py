"""Phase-1A Tests: Onboarding-/Eingabe-Seiten rendern und persistieren.

Alle Schreib-Tests biegen ``src.config.CONFIG_DIR`` per monkeypatch auf ein
tmp-Verzeichnis um — so wird NIE eine echte ``config/profile.json`` /
``config/settings.json`` im Repo angelegt. Die ``*.example.json`` werden in das
tmp-Verzeichnis kopiert, damit der Fallback (load_* ohne echte Datei) greift.
"""

import json
import shutil

import pytest
from starlette.testclient import TestClient

from src import config
from src.web.app import app

client = TestClient(app)

# Repo-Root/config mit den example-Dateien (Quelle für die tmp-Kopie).
REAL_CONFIG_DIR = config.CONFIG_DIR


@pytest.fixture()
def tmp_config(tmp_path, monkeypatch):
    """Leitet config.CONFIG_DIR auf tmp_path um und kopiert die example-Configs.

    Gibt das tmp-Verzeichnis zurück; Tests lesen darin profile.json/settings.json.
    """
    for name in ("profile.example.json", "settings.example.json"):
        shutil.copy(REAL_CONFIG_DIR / name, tmp_path / name)
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    return tmp_path


# ─── Rendering (200) ─────────────────────────────────────────

def test_plan_renders():
    r = client.get("/plan")
    assert r.status_code == 200
    assert "Sirdar" in r.text


def test_profile_renders():
    r = client.get("/profile")
    assert r.status_code == 200


def test_goals_renders():
    r = client.get("/goals")
    assert r.status_code == 200


def test_settings_renders():
    r = client.get("/settings")
    assert r.status_code == 200
    # Strava-Warnhinweis muss im Markup vorhanden sein (DE-Default).
    assert "Strava" in r.text


def test_exercise_row_partial():
    """HTMX-Partial liefert eine einzelne Übungszeile (kein volles Layout)."""
    r = client.get("/profile/exercise-row")
    assert r.status_code == 200
    assert 'name="ex_exercise"' in r.text
    assert "<html" not in r.text.lower()


def test_nav_links_present():
    """Bottom-Nav verlinkt die echten Routen und markiert die aktive Seite."""
    r = client.get("/profile")
    assert 'href="/profile"' in r.text
    assert 'href="/goals"' in r.text
    assert 'href="/settings"' in r.text
    assert 'href="/plan"' in r.text
    assert 'aria-current="page"' in r.text  # /profile ist aktiv


# ─── Persistierung: Profil ───────────────────────────────────

def test_profile_post_persists(tmp_config):
    """POST /profile schreibt nach tmp config/profile.json inkl. Übungsliste."""
    r = client.post(
        "/profile",
        data={
            "name": "Max",
            "birth_year": "1990",
            "sex": "m",
            "height_cm": "182",
            "weight_kg": "76.5",
            "cycling_enabled": "on",
            "cycling_experience": "advanced",
            "ftp_watts": "265",
            "hr_max": "190",
            "zone_model": "coggan7",
            "cycling_indoor": "on",
            "has_powermeter": "on",
            "cycling_weekly_hours": "8",
            "cycling_weekly_km": "200",
            "strength_enabled": "on",
            "strength_experience": "intermediate",
            "preferred_split": "upper_lower",
            "equipment_available": "Langhantel, Kurzhanteln , Klimmzugstange",
            "strength_frequency": "3",
            # Zwei Übungen + eine leere Zeile (soll rausfallen).
            "ex_exercise": ["Kniebeuge", "Bankdrücken", ""],
            "ex_sets": ["3", "4", ""],
            "ex_reps": ["5", "8", ""],
            "ex_weight": ["100", "70.5", ""],
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    assert r.headers["location"] == "/profile?saved=1"

    saved = json.loads((tmp_config / "profile.json").read_text(encoding="utf-8"))
    assert saved["name"] == "Max"
    assert saved["birth_year"] == 1990
    assert saved["weight_kg"] == 76.5
    cyc = saved["sports"]["cycling"]
    assert cyc["enabled"] is True
    assert cyc["ftp_watts"] == 265
    assert cyc["outdoor"] is False  # Checkbox nicht gesetzt
    assert cyc["current_state"]["weekly_km"] == 200
    strg = saved["sports"]["strength"]
    assert strg["equipment_available"] == ["Langhantel", "Kurzhanteln", "Klimmzugstange"]
    ex = strg["current_state"]["current_exercises"]
    assert len(ex) == 2  # leere Zeile wurde verworfen
    assert ex[0] == {"exercise": "Kniebeuge", "sets": 3, "reps": 5, "weight_kg": 100.0}
    assert ex[1]["weight_kg"] == 70.5


def test_profile_post_merges_unknown_keys(tmp_config):
    """Felder, die das Formular nicht kennt (z.B. constraints), bleiben erhalten."""
    # Vorab ein Profil mit Zusatz-Feld speichern.
    base = config.load_profile()
    base["constraints"] = {"injuries_notes": "Knie links"}
    config.save_profile(base)

    client.post("/profile", data={"name": "Tester"}, follow_redirects=False)

    saved = json.loads((tmp_config / "profile.json").read_text(encoding="utf-8"))
    assert saved["name"] == "Tester"
    assert saved["constraints"]["injuries_notes"] == "Knie links"  # nicht verloren


# ─── Persistierung: Ziele ────────────────────────────────────

def test_goals_add_edit_delete(tmp_config):
    """Ziel anlegen, bearbeiten und löschen — jeweils persistiert in profile.json."""
    # Start: example hat ein Beispielziel ("example").
    # Neues Ziel anlegen (ohne goal_id -> generierte id).
    client.post(
        "/goals",
        data={
            "goal_id": "",
            "type": "ftp_increase",
            "sport": "cycling",
            "baseline": "250",
            "target": "280",
            "event_date": "2026-09-01",
            "priority": "primary",
            "horizon_weeks": "12",
        },
        follow_redirects=False,
    )
    goals = json.loads((tmp_config / "profile.json").read_text(encoding="utf-8"))["goals"]
    # example.json hat bereits ein "example"-Ziel — unser neues hat baseline "250".
    new = [g for g in goals if g["baseline"] == "250"]
    assert len(new) == 1
    gid = new[0]["id"]
    assert gid != "example"
    assert new[0]["target"] == "280"

    # Bearbeiten (gleiche id -> ersetzt, kein Duplikat).
    client.post(
        "/goals",
        data={"goal_id": gid, "type": "ftp_increase", "sport": "cycling",
              "target": "300", "priority": "maintenance", "horizon_weeks": "16"},
        follow_redirects=False,
    )
    goals = json.loads((tmp_config / "profile.json").read_text(encoding="utf-8"))["goals"]
    edited = [g for g in goals if g["id"] == gid]
    assert len(edited) == 1
    assert edited[0]["target"] == "300"
    assert edited[0]["priority"] == "maintenance"

    # Löschen.
    client.post(f"/goals/{gid}/delete", follow_redirects=False)
    goals = json.loads((tmp_config / "profile.json").read_text(encoding="utf-8"))["goals"]
    assert all(g["id"] != gid for g in goals)


# ─── Persistierung: Settings ─────────────────────────────────

def test_settings_post_persists(tmp_config):
    """POST /settings schreibt nach tmp config/settings.json inkl. Integrationen."""
    r = client.post(
        "/settings",
        data={
            "locale": "en",
            "units": "imperial",
            "web_host": "127.0.0.1",
            "web_port": "9000",
            "autonomy": "auto",
            "file_import_enabled": "on",
            "weather_enabled": "on",
            "weather_latitude": "48.1",
            "weather_longitude": "11.5",
            "strava_enabled": "on",
            "strava_client_id": "abc",
            "strava_client_secret": "sekret",
            "ors_api_key": "key123",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303
    saved = json.loads((tmp_config / "settings.json").read_text(encoding="utf-8"))
    assert saved["locale"] == "en"
    assert saved["units"] == "imperial"
    assert saved["web"]["port"] == 9000
    assert saved["ai"]["autonomy"] == "auto"
    integ = saved["integrations"]
    assert integ["weather_open_meteo"]["enabled"] is True
    assert integ["weather_open_meteo"]["latitude"] == 48.1
    assert integ["strava"]["enabled"] is True
    assert integ["strava"]["client_secret"] == "sekret"
    assert integ["calendar_caldav"]["enabled"] is False  # nicht gesendet -> aus
    assert integ["openrouteservice"]["api_key"] == "key123"
