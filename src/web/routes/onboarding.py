"""Onboarding-/Eingabe-Routen für Sirdar (Phase 1A).

Seiten:
  GET  /plan                 -> Trainingsplan-Platzhalter (kommt in Phase 1)
  GET  /profile              -> Profil-/Ist-Zustand-Formular (profile.json)
  POST /profile              -> speichert (merged) nach config/profile.json
  GET  /profile/exercise-row -> HTMX-Partial: eine leere Kraft-Übungszeile
  GET  /goals                -> Ziele-Liste (profile.json -> goals[])
  POST /goals                -> Ziel hinzufügen/bearbeiten
  POST /goals/{goal_id}/delete -> Ziel löschen
  GET  /settings             -> Settings-Formular (settings.json)
  POST /settings             -> speichert (merged) nach config/settings.json

Persistenz: Es wird IMMER zuerst das vorhandene Profil/Settings via
``load_profile``/``load_settings`` geladen, dann mit den Formularwerten gemerged
und via ``save_profile``/``save_settings`` (atomic write) zurückgeschrieben — so
gehen Felder nie verloren, auch wenn das Formular sie nicht kennt.
Die dynamische Kraft-Übungsliste wird über parallele Form-Arrays
(``ex_exercise``/``ex_sets``/``ex_reps``/``ex_weight``) übertragen und Zeilen via
HTMX hinzugefügt/entfernt.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from src.config import load_profile, load_settings, save_profile, save_settings
from src.web.deps import ctx, templates

router = APIRouter()


# ─── Hilfsfunktionen: Form-Werte -> typsicher ────────────────────────────

def _to_int(value: str | None) -> int | None:
    """'42' -> 42, '' / None / Müll -> None (leere Felder bleiben None)."""
    if value is None:
        return None
    value = value.strip()
    if value == "":
        return None
    try:
        return int(float(value))  # erlaubt '42.0'
    except (ValueError, TypeError):
        return None


def _to_float(value: str | None) -> float | None:
    """'62.5' -> 62.5, '' / None / Müll -> None."""
    if value is None:
        return None
    value = value.strip()
    if value == "":
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def _to_str(value: str | None) -> str:
    """Trimmt; gibt '' für None zurück."""
    return (value or "").strip()


def _checkbox(value: str | None) -> bool:
    """HTML-Checkbox: anwesend (z.B. 'on'/'true') -> True, sonst False."""
    return value is not None and value != ""


# ─── /plan — Platzhalter ─────────────────────────────────────────────────

@router.get("/plan", response_class=HTMLResponse)
async def plan(request: Request):
    """Trainingsplan-Platzhalter — die echte Plan-Ansicht folgt in Phase 1."""
    return templates.TemplateResponse(request, "plan.html", ctx(request, "plan"))


# ─── /profile — Profil & Ist-Zustand ─────────────────────────────────────

@router.get("/profile", response_class=HTMLResponse)
async def profile_page(request: Request, saved: bool = False):
    """Zeigt das Profilformular, vorbefüllt aus dem aktuellen Profil."""
    profile = load_profile()
    return templates.TemplateResponse(
        request, "profile.html", ctx(request, "profile", profile=profile, saved=saved)
    )


@router.get("/profile/exercise-row", response_class=HTMLResponse)
async def exercise_row(request: Request):
    """HTMX-Partial: liefert eine leere Kraft-Übungszeile zum Anhängen."""
    empty = {"exercise": "", "sets": None, "reps": None, "weight_kg": None}
    return templates.TemplateResponse(
        request, "partials/exercise_row.html", ctx(request, "profile", ex=empty)
    )


@router.post("/profile")
async def profile_save(request: Request):
    """Merged die Formularwerte ins geladene Profil und speichert es.

    Wir lesen das rohe Form-Dict (statt vieler Form(...)-Parameter), weil die
    Kraft-Übungen als parallele Arrays kommen und ``request.form()`` mehrfach
    vorkommende Keys via ``getlist`` korrekt liefert.
    """
    form = await request.form()
    profile = load_profile()

    # — Persönliches —
    profile["name"] = _to_str(form.get("name"))
    profile["birth_year"] = _to_int(form.get("birth_year"))
    profile["sex"] = _to_str(form.get("sex")) or None
    profile["height_cm"] = _to_float(form.get("height_cm"))
    profile["weight_kg"] = _to_float(form.get("weight_kg"))

    sports = profile.setdefault("sports", {})

    # — Rad (cycling) —
    cycling = sports.setdefault("cycling", {})
    cycling["enabled"] = _checkbox(form.get("cycling_enabled"))
    cycling["experience"] = _to_str(form.get("cycling_experience")) or cycling.get("experience")
    cycling["ftp_watts"] = _to_int(form.get("ftp_watts"))
    cycling["hr_max"] = _to_int(form.get("hr_max"))
    cycling["zone_model"] = _to_str(form.get("zone_model")) or cycling.get("zone_model")
    cycling["indoor"] = _checkbox(form.get("cycling_indoor"))
    cycling["outdoor"] = _checkbox(form.get("cycling_outdoor"))
    cycling["has_powermeter"] = _checkbox(form.get("has_powermeter"))
    cyc_state = cycling.setdefault("current_state", {})
    cyc_state["weekly_hours"] = _to_float(form.get("cycling_weekly_hours"))
    cyc_state["weekly_km"] = _to_float(form.get("cycling_weekly_km"))

    # — Kraft (strength) —
    strength = sports.setdefault("strength", {})
    strength["enabled"] = _checkbox(form.get("strength_enabled"))
    strength["experience"] = _to_str(form.get("strength_experience")) or strength.get("experience")
    strength["preferred_split"] = _to_str(form.get("preferred_split")) or strength.get("preferred_split")
    # Equipment als Komma-Liste -> bereinigte Liste
    equipment_raw = _to_str(form.get("equipment_available"))
    strength["equipment_available"] = [
        item.strip() for item in equipment_raw.split(",") if item.strip()
    ]
    str_state = strength.setdefault("current_state", {})
    str_state["frequency_per_week"] = _to_int(form.get("strength_frequency"))

    # Dynamische Übungs-Zeilen (parallele Arrays). Leere Zeilen (ohne Namen) raus.
    names = form.getlist("ex_exercise")
    sets = form.getlist("ex_sets")
    reps = form.getlist("ex_reps")
    weights = form.getlist("ex_weight")
    exercises: list[dict] = []
    for i, name in enumerate(names):
        name = _to_str(name)
        s = _to_int(sets[i]) if i < len(sets) else None
        r = _to_int(reps[i]) if i < len(reps) else None
        w = _to_float(weights[i]) if i < len(weights) else None
        if not name and s is None and r is None and w is None:
            continue  # komplett leere Zeile überspringen
        exercises.append({"exercise": name, "sets": s, "reps": r, "weight_kg": w})
    str_state["current_exercises"] = exercises

    save_profile(profile)
    return RedirectResponse("/profile?saved=1", status_code=303)


# ─── /goals — Ziele (liegen in profile.json -> goals[]) ──────────────────

@router.get("/goals", response_class=HTMLResponse)
async def goals_page(request: Request, edit: str | None = None, saved: bool = False):
    """Listet Ziele und zeigt das Formular (Hinzufügen oder Bearbeiten)."""
    profile = load_profile()
    goals = profile.get("goals", []) or []
    editing = None
    if edit:
        editing = next((g for g in goals if str(g.get("id")) == edit), None)
    return templates.TemplateResponse(
        request, "goals.html",
        ctx(request, "goals", goals=goals, editing=editing, saved=saved),
    )


@router.post("/goals")
async def goals_save(
    request: Request,
    goal_id: str = Form(""),
    type: str = Form(""),
    sport: str = Form(""),
    baseline: str = Form(""),
    target: str = Form(""),
    event_date: str = Form(""),
    priority: str = Form("primary"),
    horizon_weeks: str = Form(""),
):
    """Fügt ein Ziel hinzu oder aktualisiert ein bestehendes (per id)."""
    profile = load_profile()
    goals = profile.setdefault("goals", [])

    goal = {
        "id": _to_str(goal_id) or uuid.uuid4().hex[:8],
        "type": _to_str(type),
        "sport": _to_str(sport),
        "baseline": _to_str(baseline) or None,
        "target": _to_str(target) or None,
        "event_date": _to_str(event_date) or None,
        "priority": _to_str(priority) or "primary",
        "horizon_weeks": _to_int(horizon_weeks),
    }

    # Bestehendes Ziel ersetzen, sonst anhängen.
    for i, existing in enumerate(goals):
        if str(existing.get("id")) == goal["id"]:
            goals[i] = goal
            break
    else:
        goals.append(goal)

    save_profile(profile)
    return RedirectResponse("/goals?saved=1", status_code=303)


@router.post("/goals/{goal_id}/delete")
async def goals_delete(request: Request, goal_id: str):
    """Löscht ein Ziel anhand seiner id."""
    profile = load_profile()
    goals = profile.get("goals", []) or []
    profile["goals"] = [g for g in goals if str(g.get("id")) != goal_id]
    save_profile(profile)
    return RedirectResponse("/goals", status_code=303)


# ─── /settings — Einstellungen ───────────────────────────────────────────

@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request, saved: bool = False):
    """Zeigt das Settings-Formular, vorbefüllt aus den aktuellen Settings."""
    settings = load_settings()
    return templates.TemplateResponse(
        request, "settings.html", ctx(request, "settings", settings=settings, saved=saved)
    )


@router.post("/settings")
async def settings_save(request: Request):
    """Merged die Formularwerte in die geladenen Settings und speichert sie."""
    form = await request.form()
    settings = load_settings()

    # — Allgemein —
    settings["locale"] = _to_str(form.get("locale")) or settings.get("locale", "de")
    settings["units"] = _to_str(form.get("units")) or settings.get("units", "metric")

    # — Web —
    web = settings.setdefault("web", {})
    web["host"] = _to_str(form.get("web_host")) or web.get("host", "0.0.0.0")
    web["port"] = _to_int(form.get("web_port")) or web.get("port", 8000)

    # — KI-Autonomie —
    ai = settings.setdefault("ai", {})
    ai["autonomy"] = _to_str(form.get("autonomy")) or ai.get("autonomy", "suggest")

    # — Integrationen —
    integ = settings.setdefault("integrations", {})

    fi = integ.setdefault("file_import", {})
    fi["enabled"] = _checkbox(form.get("file_import_enabled"))

    wx = integ.setdefault("weather_open_meteo", {})
    wx["enabled"] = _checkbox(form.get("weather_enabled"))
    wx["latitude"] = _to_float(form.get("weather_latitude"))
    wx["longitude"] = _to_float(form.get("weather_longitude"))

    cal = integ.setdefault("calendar_caldav", {})
    cal["enabled"] = _checkbox(form.get("calendar_enabled"))
    cal["url"] = _to_str(form.get("calendar_url"))
    cal["username"] = _to_str(form.get("calendar_username"))
    cal["password"] = _to_str(form.get("calendar_password"))

    ah = integ.setdefault("apple_health_push", {})
    ah["enabled"] = _checkbox(form.get("apple_health_enabled"))
    ah["shared_secret"] = _to_str(form.get("apple_health_secret"))

    gar = integ.setdefault("garmin", {})
    gar["enabled"] = _checkbox(form.get("garmin_enabled"))
    gar["client_id"] = _to_str(form.get("garmin_client_id"))
    gar["client_secret"] = _to_str(form.get("garmin_client_secret"))

    strava = integ.setdefault("strava", {})
    strava["enabled"] = _checkbox(form.get("strava_enabled"))
    strava["client_id"] = _to_str(form.get("strava_client_id"))
    strava["client_secret"] = _to_str(form.get("strava_client_secret"))

    ors = integ.setdefault("openrouteservice", {})
    ors["enabled"] = _checkbox(form.get("ors_enabled"))
    ors["api_key"] = _to_str(form.get("ors_api_key"))

    save_settings(settings)
    return RedirectResponse("/settings?saved=1", status_code=303)
