"""Coach-Brain für Sirdar (Phase 1D) — die KI-gestützte, konversationelle Plan-Generierung.

Das Herzstück: baut aus Profil + Sportarten + Ist-Zustand + Zielen + Last (CTL/ATL/TSB)
+ Readiness + Settings einen strukturierten Kontext (``build_context``), gibt diesen
zusammen mit dem ``SYSTEM_PROMPT`` (KONZEPT §6) an die Claude Code CLI (``ask_claude``)
und persistiert den zurückgelieferten Plan in ``plan_days``.

Kostet 0 € API-Gebühren: läuft über das eigene Claude-Abo (CLI), nicht die API
(siehe src/core/claude.py).

Design (vom Nutzer entschieden, siehe KONZEPT §6 + Briefing):
  - Ton: ausgewogen (fundiert + motivierend, erklärt das „Warum").
  - Kraft-Progression UND Rad-Intensitätsverteilung wählt die KI je nach Ziel/Level
    (der Prompt kennt die Optionen und begründet die passende Wahl).
  - Flexibler Planungs-Horizont: block (~4 Wochen) | week (7 Tage) | day (heute/morgen),
    Default aus settings.ai.planning_horizon, im Chat ad hoc überschreibbar.
  - Bei Neuplanung werden NUR zukünftige/heutige Tage überschrieben; vergangene und
    bereits erledigte (status='done') Tage bleiben unangetastet.
  - Daten-Integrität (KONZEPT §6.3): keine Werte erfinden — fehlt FTP/Ist-Zustand/Ziel,
    leitet der Coach freundlich zum Onboarding statt zu raten.

Robustheit: Fehlt das Claude-Binary/Token oder ist die Antwort leer/ungültig, wird eine
saubere Exception (``CoachError`` / ``ClaudeCLIError``) geworfen — nie ein Crash, nie der
Fehlertext als Coach-Antwort.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path

from src.config import load_profile, load_settings
from src.core.claude import ClaudeCLIError, ask_claude, strip_json_block
from src.core.db import get_connection, init_db
from src.core.load import get_load_series, latest_load, weekly_tss
from src.core.readiness import readiness_for_date
from src.data.store import recent_workouts

logger = logging.getLogger(__name__)


class CoachError(RuntimeError):
    """Plan-Generierung fehlgeschlagen (ungültige/leere KI-Antwort, Validierung).

    Abgegrenzt von ``ClaudeCLIError`` (CLI-Aufruf selbst). Beide werden vom
    Web-Layer gefangen und als freundliche Meldung gezeigt — nie als Crash.
    """


# ── Konstanten ────────────────────────────────────────────────────────────

# Gültige Horizont-Modi (KONZEPT-Briefing). Default kommt aus settings.
HORIZONS = ("block", "week", "day")
_DEFAULT_HORIZON = "week"

# Wie viele Tage je Horizont geplant werden (Obergrenze für die Zukunfts-Persistierung).
_HORIZON_DAYS = {"block": 28, "week": 7, "day": 2}

# Erlaubte Werte im Plan-JSON (grobe Validierung, nicht erfunden).
_VALID_SPORTS = {"cycling", "strength", "rest", "other"}
_VALID_INTENSITIES = {"easy", "moderate", "hard"}
_VALID_PHASES = {"base", "build", "peak", "taper", "maintenance"}
_VALID_WEEK_TYPES = {"load", "recovery"}

# Status, der bei Neuplanung NICHT überschrieben wird (bereits erledigt).
_PROTECTED_STATUS = "done"

# Wie viele letzte Workouts in den Kontext fließen.
_RECENT_WORKOUTS = 12
# Wie viele Tage der Last-Serie (für TSB-Trend) in den Kontext fließen.
_LOAD_TREND_DAYS = 14


# ════════════════════════════════════════════════════════════════════════════
#  SYSTEM PROMPT (KONZEPT §6 — sorgfältig; der Haupt-Agent reviewt diesen genau)
# ════════════════════════════════════════════════════════════════════════════

SYSTEM_PROMPT = """\
Du bist Sirdar, ein erfahrener Ausdauer- und Kraft-Trainingscoach für einen einzelnen \
Athleten (Rennrad + Krafttraining). Du planst und führst wie ein guter persönlicher \
Coach: fundiert UND motivierend. Du erklärst stets das „Warum" hinter einer Einheit, \
ohne zu plaudern. Dein Ton ist ausgewogen — sachlich-kompetent, aber zugewandt; kein \
Hype, keine Phrasen, keine Emoji-Flut.

Du erhältst als Kontext ein JSON mit: Profil, aktivierten Sportarten inkl. Ist-Zustand \
(Geräte, aktuelle Übungen + Gewichte, FTP, Wochenstunden), Zielen, letzten Workouts, \
aktueller Trainingslast (CTL/ATL/TSB), Wochen-TSS, heutiger Readiness und Settings \
(Ton, Autonomie, Horizont). Plane ausschließlich auf Basis dieser Daten.

══════════════════════════════════════════════════════════════════════════════
TRAININGSWISSENSCHAFTLICHES REGELWERK (verbindlich)
══════════════════════════════════════════════════════════════════════════════

MENTALES MODELL
- Belastung → Erholung → Anpassung. Drei Hebel: Frequenz, Volumen, Intensität —
  niemals mehrere gleichzeitig hochfahren. Im Zweifel reduzieren.

NORMIERUNG
- Rad: über FTP. Coggan-Zonen (Z1 Recovery <55 %, Z2 Endurance 56–75 %, Z3 Tempo
  76–90 %, Z4 Threshold/Sweet-Spot 88–105 %, Z5 VO2max 106–120 %, Z6 Anaerob >120 %
  FTP). TSS ≈ (Dauer_h × IF² × 100), IF = NP/FTP. Watt-Vorgaben immer als % FTP
  ableiten und absolut (in Watt) angeben, sofern FTP bekannt ist.
- Kraft: über Arbeitsgewichte + Ziel-RIR (Reps in Reserve). Gewichte aus dem
  erfassten Ist-Zustand ableiten und progressiv steigern.

LAST STEUERN — HARTE LEITPLANKE
- CTL = Fitness (42-Tage-Schnitt der TSS), ATL = Fatigue (7 Tage), TSB = CTL−ATL = Form.
- Ramp Rate: CTL darf um MAXIMAL +3 bis +5 pro Woche steigen. Das ist eine HARTE
  Grenze — überschreite sie nie, auch nicht auf Wunsch des Athleten (erkläre warum).
- TSB-Bewusstsein: stark negativer TSB → vorsichtig dosieren / Erholung priorisieren;
  positiver TSB vor einem Event = Frische (Taper).

PERIODISIERUNG
- Base → Build → Peak → Taper (an Ziel/Event-Datum ausrichten); ohne Event:
  Base/Build mit Maintenance des Sekundärziels.
- Wochenrhythmus 3:1 (drei Belastungswochen, dann eine Erholungs-/Deload-Woche).
- Polarisierung: 60–90 % des AUSDAUER-Volumens niedrigintensiv (Z1/Z2) als robuster
  Default. Hochintensive Einheiten gezielt und sparsam.

INTENSITÄTSVERTEILUNG RAD — du WÄHLST das Modell je nach Ziel/Level und begründest es:
- polarisiert (≈80/20, viel Z2 + harte Z5-Blöcke) — gut für Erfahrene/hohes Volumen.
- pyramidal (mehr Schwellen-/Sweet-Spot-Anteil) — gut für zeitbegrenzte Athleten.
- Sweet-Spot-fokussiert (88–94 % FTP) — effizienter FTP-Aufbau bei wenig Zeit.
Gib NICHT starr ein Modell vor — passe es an den Athleten an.

KRAFT-PROGRESSION — du WÄHLST die Methode je nach Ziel/Level und begründest sie:
- RIR-autoreguliert (Sätze nach Ziel-RIR steuern) — robust, alltagstauglich.
- Double-Progression (erst Wdh. im Bereich steigern, dann Gewicht) — gut für Hypertrophie.
- lineare Progression (kleine, feste Gewichtssprünge) — gut für Anfänger.
Leite konkrete Gewichte aus dem Ist-Zustand ab und steigere progressiv über die Wochen.

CONCURRENT TRAINING (Rad + Kraft)
- Beinkraft (Kniebeuge, Kreuzheben etc.) mit zeitlichem Abstand zu Schlüssel-Radeinheiten
  (idealerweise nicht am selben oder direkt vor einem harten Radtag).
- Harte Tage bündeln, leichte Tage bündeln (so bleiben echte Erholungstage erhalten).
- Nach dem PRIMÄRZIEL sequenzieren: ist Rad das Primärziel, hat die Schlüssel-Radeinheit
  Vorrang; Kraft wird darum herum gelegt (und umgekehrt).
- Erholungstage schützen — nicht „nur kurz" volllaufen lassen.

READINESS-BEWUSSTSEIN (Tagesform-Ampel)
- 🟢 grün: Plan wie vorgesehen.
- 🟡 gelb: harte Einheit abschwächen (z. B. VO2max → Sweet-Spot, Kraft-RIR +1).
- 🔴 rot: harte Einheit → Recovery/Ruhe, mit einem späteren Tag tauschen.
- unknown: keine seriöse Aussage möglich → plane neutral nach Plan, nichts erfinden.
- Verschieben statt streichen · Block-/Wochen-Integrität wahren · Adhärenz > theoretisches
  Optimum.

ZIEL-PRIORISIERUNG
- Genau EIN Primärziel treibt die Periodisierung. Alle weiteren Ziele laufen auf
  Maintenance (Erhalt), bis das Primärziel erreicht/abgeschlossen ist.

══════════════════════════════════════════════════════════════════════════════
DATEN-INTEGRITÄT (KONZEPT §6.3) — STRIKT
══════════════════════════════════════════════════════════════════════════════
- Erfinde KEINE Werte. Nutze nur, was im Kontext steht.
- Fehlt für eine sinnvolle Planung Wesentliches (z. B. FTP fürs Rad, aktuelle
  Übungen/Gewichte für Kraft, oder jegliches Ziel), dann PLANE NICHT auf geratenen
  Zahlen. Erkläre freundlich, was fehlt, und bitte den Athleten, es im Onboarding
  (Profil/Ziele) zu ergänzen. Gib in diesem Fall einen leeren "days"-Array zurück.
- Bezieh dich auf reale Zahlen aus dem Kontext (z. B. „bei deiner FTP von 250 W").

══════════════════════════════════════════════════════════════════════════════
HORIZONT
══════════════════════════════════════════════════════════════════════════════
Der gewünschte Horizont steht im Kontext (oder in der Anweisung des Athleten):
- "block": ~4 Wochen Wochenstruktur mit 3:1-Rhythmus und Phase; Tagesdetail für die
  AKTUELLE Woche (mind. die nächsten 7 Tage als konkrete Einheiten), spätere Wochen
  dürfen gröber sein, aber jeder Tag im "days"-Array braucht ein Datum.
- "week": die nächsten 7 Tage als konkrete Einheiten.
- "day": heute (und ggf. morgen) als konkrete Einheit(en).
Plane immer ab HEUTE (oder dem im Kontext genannten „heute") vorwärts — niemals
Tage in der Vergangenheit.

══════════════════════════════════════════════════════════════════════════════
AUSGABE-VERTRAG (verbindlich)
══════════════════════════════════════════════════════════════════════════════
Antworte mit GENAU ZWEI Teilen, in dieser Reihenfolge:
(a) Eine kurze, ausgewogene Erklärung in PROSA (Deutsch, sofern der Athlet nicht
    Englisch schreibt): was du planst und WARUM (Phase, Last-Logik, Verteilung,
    Concurrent-/Readiness-Anpassungen). Knapp, aber begründet.
(b) GENAU EINEN abschließenden ```json-Block, exakt in diesem Schema:

```json
{
  "summary": "kurzes Warum/Was in einem Satz",
  "block": {"phase": "base|build|peak|taper|maintenance", "week_type": "load|recovery", "horizon": "block|week|day"},
  "days": [
    {"date":"YYYY-MM-DD","sport":"cycling|strength|rest|other","title":"...","type":"...","intensity":"easy|moderate|hard","rationale":"warum heute",
     "cycling":{"target":"z.B. 5x4min @110% FTP","duration_min":75,"target_tss":90,"zones":"..."},
     "strength":{"exercises":[{"exercise":"Kniebeuge","sets":4,"reps":5,"weight_kg":100,"rir":2}]}}
  ]
}
```

Regeln zum JSON:
- Das "cycling"-Objekt NUR bei sport="cycling", das "strength"-Objekt NUR bei
  sport="strength". Bei sport="rest" beide weglassen.
- "date" ist Pflicht und im Format YYYY-MM-DD. "rationale" pro Tag ist Pflicht (das „Warum").
- Watt/Gewichte aus dem erfassten Ist-Zustand ableiten und progressiv steigern; nichts erfinden.
- Wenn du mangels Daten nicht seriös planen kannst: "days": [] und erkläre in (a), was fehlt.
- Es darf NUR EIN ```json-Block in deiner Antwort sein (der Plan). Kein weiterer Codeblock.
"""


# ════════════════════════════════════════════════════════════════════════════
#  KONTEXT-AUFBAU
# ════════════════════════════════════════════════════════════════════════════

def _today_iso() -> str:
    """Heutiges Datum als ISO-String (eigene Funktion → in Tests mockbar)."""
    return date.today().isoformat()


def _clean_sport_state(sport_cfg: dict) -> dict:
    """Entfernt interne ``_comment``-Keys aus einem Sport-Config-Dict (rekursiv flach)."""
    if not isinstance(sport_cfg, dict):
        return {}
    cleaned: dict = {}
    for k, v in sport_cfg.items():
        if k.startswith("_"):
            continue
        if isinstance(v, dict):
            cleaned[k] = {kk: vv for kk, vv in v.items() if not kk.startswith("_")}
        else:
            cleaned[k] = v
    return cleaned


def build_context(db_path: str | Path | None = None) -> dict:
    """Baut den strukturierten Coach-Kontext (Dict, JSON-serialisierbar).

    Robust bei leeren Daten: fehlende Profil-/DB-Werte ergeben None/[]/leere Dicts,
    nie eine Exception. Der Coach (SYSTEM_PROMPT) entscheidet selbst, ob die Daten
    für eine seriöse Planung reichen (Daten-Integrität).

    Returns:
        Dict mit Schlüsseln: ``today``, ``profile``, ``sports``, ``goals``,
        ``recent_workouts``, ``load`` (latest CTL/ATL/TSB), ``load_trend``,
        ``weekly_tss``, ``readiness``, ``settings``, ``data_gaps``.
    """
    profile = load_profile() or {}
    settings = load_settings() or {}

    sports_raw = profile.get("sports", {}) or {}
    sports: dict = {}
    for name, cfg in sports_raw.items():
        cleaned = _clean_sport_state(cfg)
        if cleaned.get("enabled", True):  # nur aktivierte Sportarten in den Kontext
            sports[name] = cleaned

    goals = [g for g in (profile.get("goals", []) or []) if g.get("id") != "example"]

    ai = settings.get("ai", {}) or {}
    coach_settings = {
        "tone": ai.get("coach_tone", "balanced"),
        "autonomy": ai.get("autonomy", "suggest"),
        "planning_horizon": ai.get("planning_horizon", _DEFAULT_HORIZON),
    }

    # Profil ohne Sport-Block (der kommt separat + bereinigt) und ohne _comment.
    profile_core = {
        k: v for k, v in profile.items()
        if k not in ("sports", "goals") and not k.startswith("_")
    }

    # Daten-Lücken explizit markieren — der Prompt nutzt das für die Integritäts-Regel.
    data_gaps = _detect_gaps(sports, goals)

    latest = latest_load(db_path=db_path)
    trend = get_load_series(days=_LOAD_TREND_DAYS, db_path=db_path)
    readiness = readiness_for_date(db_path=db_path)

    return {
        "today": _today_iso(),
        "profile": profile_core,
        "sports": sports,
        "goals": goals,
        "recent_workouts": recent_workouts(limit=_RECENT_WORKOUTS, db_path=db_path),
        "load": latest,  # {"datum","ctl","atl","tsb"} oder None
        "load_trend": trend,
        "weekly_tss": weekly_tss(days=7, db_path=db_path),
        "readiness": readiness,
        "settings": coach_settings,
        "data_gaps": data_gaps,
    }


def _detect_gaps(sports: dict, goals: list) -> list[str]:
    """Listet fehlende, planungsrelevante Daten auf (für die Integritäts-Regel).

    Erfindet nichts — markiert nur, was fehlt, damit der Coach freundlich zum
    Onboarding leiten kann statt zu raten.
    """
    gaps: list[str] = []
    if not goals:
        gaps.append("no_goals")

    cyc = sports.get("cycling")
    if cyc is not None and not cyc.get("ftp_watts"):
        gaps.append("cycling_no_ftp")

    strg = sports.get("strength")
    if strg is not None:
        exercises = (strg.get("current_state", {}) or {}).get("current_exercises", []) or []
        has_real = any((e.get("exercise") or "").strip() for e in exercises)
        if not has_real:
            gaps.append("strength_no_current_exercises")

    if not sports:
        gaps.append("no_sports_enabled")
    return gaps


# ════════════════════════════════════════════════════════════════════════════
#  PROMPT-BAU
# ════════════════════════════════════════════════════════════════════════════

def _resolve_horizon(horizon: str | None) -> str:
    """Gewünschter Horizont → gültiger Modus (Default aus settings.ai.planning_horizon)."""
    if horizon in HORIZONS:
        return horizon
    cfg = (load_settings().get("ai", {}) or {}).get("planning_horizon")
    return cfg if cfg in HORIZONS else _DEFAULT_HORIZON


def _build_plan_user_prompt(context: dict, horizon: str, instruction: str | None) -> str:
    """User-Prompt für die Plan-Generierung: Kontext-JSON + Horizont + Anweisung."""
    parts = [
        f"Geplanter Horizont: {horizon} "
        f"({'≈4 Wochen' if horizon == 'block' else '7 Tage' if horizon == 'week' else 'heute/morgen'}).",
        f"Heute ist {context['today']}. Plane ab heute vorwärts.",
    ]
    if instruction:
        parts.append(f"Zusätzliche Anweisung des Athleten: {instruction}")
    parts.append(
        "Erstelle bzw. aktualisiere den Trainingsplan gemäß Regelwerk und Ausgabe-Vertrag. "
        "Halte dich an die Daten-Integrität: erfinde nichts, leite bei fehlenden Daten "
        "freundlich zum Onboarding."
    )
    parts.append("\n--- KONTEXT (JSON) ---\n" + json.dumps(context, ensure_ascii=False, indent=2, default=str))
    return "\n\n".join(parts)


def _build_chat_user_prompt(context: dict, history: list[dict], message: str) -> str:
    """User-Prompt für den Coach-Chat: Kontext + bisheriger Verlauf + neue Nachricht."""
    parts = [
        f"Heute ist {context['today']}.",
        "Du führst ein Coaching-Gespräch. Antworte konversationell auf die neue Nachricht "
        "des Athleten. WENN (und nur wenn) die Nachricht eine Plan-Erstellung oder -Änderung "
        "verlangt, hänge GENAU EINEN ```json-Block gemäß Ausgabe-Vertrag an (sonst KEINEN "
        "json-Block). Reine Fragen/Erklärungen beantwortest du nur in Prosa.",
    ]
    if history:
        convo = "\n".join(
            f"{'Athlet' if h.get('rolle') == 'user' else 'Coach'}: {h.get('inhalt', '')}"
            for h in history
        )
        parts.append("--- BISHERIGER VERLAUF ---\n" + convo)
    parts.append(f"--- NEUE NACHRICHT DES ATHLETEN ---\n{message}")
    parts.append("\n--- KONTEXT (JSON) ---\n" + json.dumps(context, ensure_ascii=False, indent=2, default=str))
    return "\n\n".join(parts)


# ════════════════════════════════════════════════════════════════════════════
#  PLAN-VALIDIERUNG + PERSISTIERUNG
# ════════════════════════════════════════════════════════════════════════════

def _coerce_day(raw: dict) -> dict | None:
    """Validiert/normalisiert einen Tag aus dem KI-JSON. None, wenn unbrauchbar.

    Erwartet mind. ein gültiges ``date`` (YYYY-MM-DD). Unbekannte sport/intensity/
    phase-Werte werden konservativ behandelt (sport→'other', intensity→None), aber
    nie erfunden.
    """
    if not isinstance(raw, dict):
        return None
    raw_date = (raw.get("date") or "").strip()
    try:
        datetime.strptime(raw_date, "%Y-%m-%d")
    except (ValueError, TypeError):
        return None  # ohne valides Datum kein Plan-Tag

    sport = raw.get("sport")
    if sport not in _VALID_SPORTS:
        sport = "other"

    intensity = raw.get("intensity")
    if intensity not in _VALID_INTENSITIES:
        intensity = None

    day = {
        "date": raw_date,
        "sport": sport,
        "title": (raw.get("title") or "").strip() or None,
        "type": (raw.get("type") or "").strip() or None,
        "intensity": intensity,
        "rationale": (raw.get("rationale") or "").strip() or None,
    }
    # Sport-spezifische Details (nur beim passenden Sport behalten).
    if sport == "cycling" and isinstance(raw.get("cycling"), dict):
        day["cycling"] = raw["cycling"]
    if sport == "strength" and isinstance(raw.get("strength"), dict):
        day["strength"] = raw["strength"]
    return day


def _validate_plan(structured: dict | None) -> dict:
    """Grobe Validierung des KI-Plan-JSON. Wirft CoachError bei Untauglichkeit.

    Gibt ein normalisiertes Dict zurück: ``{"summary", "block", "days": [...]}``.
    Ein leeres ``days`` (Daten-Integrität: nicht genug Daten) ist KEIN Fehler —
    es wird sauber zurückgegeben, damit der Web-Layer den Hinweis zeigt.
    """
    if not isinstance(structured, dict):
        raise CoachError("Die KI-Antwort enthielt keinen verwertbaren Plan-JSON-Block.")

    days_raw = structured.get("days")
    if days_raw is None or not isinstance(days_raw, list):
        raise CoachError("Plan-JSON ohne gültiges 'days'-Array.")

    days = [d for d in (_coerce_day(d) for d in days_raw) if d is not None]

    block = structured.get("block") or {}
    phase = block.get("phase") if block.get("phase") in _VALID_PHASES else None
    week_type = block.get("week_type") if block.get("week_type") in _VALID_WEEK_TYPES else None

    return {
        "summary": (structured.get("summary") or "").strip() or None,
        "block": {"phase": phase, "week_type": week_type, "horizon": block.get("horizon")},
        "days": days,
    }


def _persist_plan(plan: dict, db_path: str | Path | None, max_days: int | None = None) -> list[dict]:
    """Schreibt die Plan-Tage in ``plan_days`` (UPSERT pro Datum).

    NUR Zukunft/heute werden geschrieben; vergangene Tage und bereits erledigte
    (status='done') Tage bleiben unangetastet. Gibt die tatsächlich persistierten
    Tage zurück.

    Args:
        plan: validiertes Plan-Dict (aus ``_validate_plan``).
        db_path: optionaler DB-Pfad (Tests).
        max_days: optionale Obergrenze (Horizont) für die Zahl persistierter Tage.
    """
    init_db(db_path)
    today = _today_iso()
    block = plan.get("block", {})
    phase = block.get("phase")
    week_type = block.get("week_type")

    # Tage sortieren, Vergangenes verwerfen, ggf. auf den Horizont begrenzen.
    future_days = sorted(
        (d for d in plan["days"] if d["date"] >= today),
        key=lambda d: d["date"],
    )
    if max_days is not None:
        future_days = future_days[:max_days]

    persisted: list[dict] = []
    conn = get_connection(db_path)
    try:
        for day in future_days:
            # Erledigte Tage nie überschreiben (KONZEPT §6.2: Vergangenes/Done schützen).
            existing = conn.execute(
                "SELECT status FROM plan_days WHERE datum = ?", (day["date"],)
            ).fetchone()
            if existing and existing["status"] == _PROTECTED_STATUS:
                continue

            conn.execute(
                """
                INSERT INTO plan_days (datum, geplantes_workout, status, phase, woche_typ, anpassungsgrund)
                VALUES (:datum, :geplantes_workout, 'planned', :phase, :woche_typ, :anpassungsgrund)
                ON CONFLICT(datum) DO UPDATE SET
                    geplantes_workout = excluded.geplantes_workout,
                    status            = 'planned',
                    phase             = excluded.phase,
                    woche_typ         = excluded.woche_typ,
                    anpassungsgrund   = excluded.anpassungsgrund
                """,
                {
                    "datum": day["date"],
                    "geplantes_workout": json.dumps(day, ensure_ascii=False),
                    "phase": phase,
                    "woche_typ": week_type,
                    "anpassungsgrund": day.get("rationale"),
                },
            )
            persisted.append(day)
        conn.commit()
    finally:
        conn.close()

    logger.info("plan_days aktualisiert: %d Tage persistiert (Horizont-Limit=%s).",
                len(persisted), max_days)
    return persisted


# ════════════════════════════════════════════════════════════════════════════
#  ÖFFENTLICHE API
# ════════════════════════════════════════════════════════════════════════════

def generate_plan(
    horizon: str | None = None,
    instruction: str | None = None,
    db_path: str | Path | None = None,
) -> dict:
    """Generiert (oder aktualisiert) den Trainingsplan via Claude und persistiert ihn.

    Baut den Kontext, fragt die KI, validiert die strukturierte Antwort und schreibt
    nur zukünftige/heutige Tage (UPSERT, erledigte Tage geschützt) in ``plan_days``.

    Args:
        horizon: 'block' | 'week' | 'day'. None → Default aus settings.ai.planning_horizon.
        instruction: optionale Ad-hoc-Anweisung des Athleten (z. B. „nur heute").
        db_path: optionaler DB-Pfad (Tests).

    Returns:
        ``{"summary", "block", "days": [persistierte Tage], "rationale": <Prosa-Text>}``.

    Raises:
        ClaudeCLIError: CLI nicht erreichbar / Auth / Timeout / leere Antwort.
        CoachError: Antwort ohne verwertbaren Plan-JSON / ungültiges Schema.
    """
    horizon = _resolve_horizon(horizon)
    context = build_context(db_path=db_path)
    user_prompt = _build_plan_user_prompt(context, horizon, instruction)

    result = ask_claude(SYSTEM_PROMPT, user_prompt)  # kann ClaudeCLIError werfen
    plan = _validate_plan(result.get("structured"))

    persisted = _persist_plan(plan, db_path=db_path, max_days=_HORIZON_DAYS.get(horizon))

    return {
        "summary": plan["summary"],
        "block": plan["block"],
        "days": persisted,
        "rationale": strip_json_block(result.get("text", "")),
    }


def coach_reply(
    message: str,
    history: list[dict] | None = None,
    db_path: str | Path | None = None,
) -> dict:
    """Konversationelle Coach-Antwort; persistiert Plan, falls die Antwort einen enthält.

    Speichert die User-Nachricht und die Coach-Antwort in der ``chat``-Tabelle. Enthält
    die KI-Antwort einen Plan-JSON-Block, werden die (zukünftigen) Tage wie in
    ``generate_plan`` persistiert.

    Args:
        message: neue Nachricht des Athleten.
        history: bisheriger Chat-Verlauf (Liste von ``{"rolle","inhalt"}``); None →
            wird aus der ``chat``-Tabelle geladen.
        db_path: optionaler DB-Pfad (Tests).

    Returns:
        ``{"reply": <Prosa>, "plan_updated": bool, "days": [...], "summary": ...}``.

    Raises:
        ClaudeCLIError / CoachError wie ``generate_plan``. Bei Fehler wird NICHTS
        in die chat-Tabelle geschrieben (sauberer Zustand).
    """
    message = (message or "").strip()
    if not message:
        raise CoachError("Leere Nachricht — bitte schreib dem Coach etwas.")

    history = history if history is not None else _load_chat_history(db_path=db_path)
    context = build_context(db_path=db_path)
    user_prompt = _build_chat_user_prompt(context, history, message)

    result = ask_claude(SYSTEM_PROMPT, user_prompt)  # kann ClaudeCLIError werfen
    reply_text = strip_json_block(result.get("text", "")) or result.get("text", "")

    # Plan nur persistieren, wenn ein verwertbarer JSON-Block dabei ist.
    plan_updated = False
    persisted: list[dict] = []
    summary = None
    structured = result.get("structured")
    if structured is not None:
        try:
            plan = _validate_plan(structured)
            horizon = (plan.get("block", {}) or {}).get("horizon")
            horizon = horizon if horizon in HORIZONS else _resolve_horizon(None)
            persisted = _persist_plan(plan, db_path=db_path, max_days=_HORIZON_DAYS.get(horizon))
            summary = plan.get("summary")
            plan_updated = bool(persisted)
        except CoachError:
            # Kein gültiger Plan im Chat — das ist ok (z. B. reine Erklärung mit Codeblock).
            logger.info("Chat-Antwort enthielt JSON, aber keinen gültigen Plan — nur Text.")

    # Erst NACH erfolgreichem KI-Call beide Nachrichten in chat schreiben.
    _save_chat_message("user", message, db_path=db_path)
    _save_chat_message("assistant", reply_text, db_path=db_path)

    return {
        "reply": reply_text,
        "plan_updated": plan_updated,
        "days": persisted,
        "summary": summary,
    }


# ════════════════════════════════════════════════════════════════════════════
#  CHAT-PERSISTENZ + PLAN-LESE-HELFER (für den Web-Layer)
# ════════════════════════════════════════════════════════════════════════════

def _save_chat_message(rolle: str, inhalt: str, db_path: str | Path | None = None) -> None:
    """Hängt eine Chat-Nachricht (user|assistant) an die ``chat``-Tabelle an."""
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO chat (ts, rolle, inhalt, session_id) VALUES (?, ?, ?, ?)",
            (datetime.now().isoformat(timespec="seconds"), rolle, inhalt, None),
        )
        conn.commit()
    finally:
        conn.close()


def _load_chat_history(limit: int = 20, db_path: str | Path | None = None) -> list[dict]:
    """Lädt die letzten ``limit`` Chat-Nachrichten (chronologisch aufsteigend)."""
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT rolle, inhalt FROM chat ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in reversed(rows)]
    finally:
        conn.close()


def get_chat_history(limit: int = 20, db_path: str | Path | None = None) -> list[dict]:
    """Öffentlicher Wrapper: Chat-Verlauf für die UI (chronologisch aufsteigend)."""
    return _load_chat_history(limit=limit, db_path=db_path)


def get_plan_days(
    from_date: str | None = None,
    days: int = 28,
    db_path: str | Path | None = None,
) -> list[dict]:
    """Lädt geplante Tage ab ``from_date`` (Default: heute) für die Plan-Anzeige.

    Parst das ``geplantes_workout``-JSON je Zeile in ``workout`` zurück, damit das
    Template direkt auf die präskriptiven Felder (cycling/strength) zugreifen kann.

    Returns:
        Liste von Dicts ``{"datum","status","phase","woche_typ","anpassungsgrund",
        "workout": <geparstes Dict|None>}`` (chronologisch aufsteigend).
    """
    init_db(db_path)
    start = from_date or _today_iso()
    end = (date.fromisoformat(start) + timedelta(days=max(0, days) - 1)).isoformat()
    conn = get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM plan_days WHERE datum >= ? AND datum <= ? ORDER BY datum ASC",
            (start, end),
        ).fetchall()
    finally:
        conn.close()

    result: list[dict] = []
    for r in rows:
        row = dict(r)
        workout = None
        if row.get("geplantes_workout"):
            try:
                workout = json.loads(row["geplantes_workout"])
            except (json.JSONDecodeError, TypeError):
                workout = None
        row["workout"] = workout
        result.append(row)
    return result
