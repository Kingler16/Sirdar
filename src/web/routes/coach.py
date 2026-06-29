"""Plan-/Coach-Routen für Sirdar (Phase 1D).

Seiten:
  GET  /plan            -> Plan-Ansicht: heute hervorgehoben + Horizont-Tage, je Einheit
                           präskriptiv (Kraft: Übung+Gewicht+Sätze×Wdh.+RIR; Rad:
                           Intervalle/Watt/Zonen/Dauer) + „Warum"; plus Coach-Eingabe.
  POST /plan/generate   -> erzeugt/aktualisiert den Plan via Coach (Horizont-Auswahl);
                           gibt das Plan+Coach-Partial zurück (HTMX).
  POST /plan/chat       -> konversationelle Coach-Antwort; persistiert ggf. Plan;
                           gibt das Plan+Coach-Partial zurück (HTMX).

Graceful (Briefing): ohne Ziele/FTP/Ist-Zustand → Hinweis + Link zu /profile bzw.
/goals (der Coach rät nichts, KONZEPT §6.3). Ohne Claude-CLI → klare Meldung statt
Crash. CLI-/Coach-Fehler werden gefangen und als freundlicher Banner gezeigt.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse

from src.core.claude import ClaudeCLIError, claude_available
from src.core.coach import (
    HORIZONS,
    CoachError,
    build_context,
    coach_reply,
    generate_plan,
    get_chat_history,
    get_plan_days,
)
from src.web.deps import ctx, templates

logger = logging.getLogger(__name__)

router = APIRouter()

# Wie viele Tage die Plan-Ansicht maximal zeigt (deckt den größten Horizont „block" ab).
_PLAN_VIEW_DAYS = 28


def _plan_ctx(request: Request, **extra) -> dict:
    """Baut den Plan-Template-Kontext: aktueller Plan, Chat, Setup-Status, Coach-Status."""
    context = build_context()
    plan_days = get_plan_days(days=_PLAN_VIEW_DAYS)
    today = context["today"]
    today_day = next((d for d in plan_days if d["datum"] == today), None)
    upcoming = [d for d in plan_days if d["datum"] > today]

    base = ctx(
        request, "plan",
        plan_days=plan_days,
        today=today,
        today_day=today_day,
        upcoming=upcoming,
        data_gaps=context["data_gaps"],
        chat=get_chat_history(limit=12),
        claude_ok=claude_available(),
        default_horizon=context["settings"]["planning_horizon"],
        horizons=HORIZONS,
    )
    base.update(extra)
    return base


@router.get("/plan", response_class=HTMLResponse)
async def plan_page(request: Request):
    """Zeigt den aktuellen Plan + Coach-Eingabe (volle Seite)."""
    return templates.TemplateResponse(request, "plan.html", _plan_ctx(request))


@router.post("/plan/generate", response_class=HTMLResponse)
async def plan_generate(request: Request, horizon: str = Form("week")):
    """Erzeugt/aktualisiert den Plan für den gewählten Horizont. Liefert HTMX-Partial."""
    horizon = horizon if horizon in HORIZONS else "week"
    coach_msg, coach_error, plan_updated = None, None, False
    try:
        result = generate_plan(horizon=horizon)
        coach_msg = result.get("rationale") or result.get("summary")
        plan_updated = bool(result.get("days"))
    except (ClaudeCLIError, CoachError) as exc:
        coach_error = str(exc)
        logger.warning("Plan-Generierung fehlgeschlagen: %s", exc)

    return templates.TemplateResponse(
        request, "partials/plan_body.html",
        _plan_ctx(request, coach_msg=coach_msg, coach_error=coach_error, plan_updated=plan_updated),
    )


@router.post("/plan/chat", response_class=HTMLResponse)
async def plan_chat(request: Request, message: str = Form("")):
    """Konversationelle Coach-Antwort; persistiert ggf. den Plan. Liefert HTMX-Partial."""
    coach_msg, coach_error, plan_updated = None, None, False
    try:
        result = coach_reply(message=message)
        coach_msg = result.get("reply")
        plan_updated = result.get("plan_updated", False)
    except (ClaudeCLIError, CoachError) as exc:
        coach_error = str(exc)
        logger.warning("Coach-Chat fehlgeschlagen: %s", exc)

    return templates.TemplateResponse(
        request, "partials/plan_body.html",
        _plan_ctx(request, coach_msg=coach_msg, coach_error=coach_error, plan_updated=plan_updated),
    )
