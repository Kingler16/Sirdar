"""Tägliche Readiness-Auto-Regulation für Sirdar (Phase 2B, KONZEPT §6.2).

Deterministisch, regelbasiert, KEIN Claude-Aufruf: die geplante Tageseinheit wird
anhand der heutigen Readiness-Ampel (🟢/🟡/🔴/❔) automatisch an die Tagesform
angepasst. Läuft täglich per Cron (Modus ``regulate``), kostet 0 € und ist schnell
& testbar.

Anpassungsregeln (KONZEPT §6.2):
  🟢 green / ❔ unknown → Plan unverändert.
  🟡 yellow  → die geplante HARTE Einheit abschwächen:
       - cycling intensity=='hard': → 'moderate', Ziel-TSS/Dauer ~25 % runter,
         title/target mit Hinweis. Rad nicht-hart bleibt unverändert.
       - strength: Ziel-RIR jeder Übung +1; bei >2 Sätzen einen Satz streichen.
       - leichte/Recovery-Einheiten bleiben unverändert.
  🔴 red     → harte Einheit → Recovery/Ruhe (cycling Z1 30–45 min ODER sport=rest);
       leichte Einheiten ggf. zu Ruhe.

Originalplan-Schutz: das ursprüngliche Workout wird unter ``workout["original"]``
hinterlegt, damit die Anpassung nachvollziehbar/zurücknehmbar ist. Eine bereits
gesicherte ``original`` wird NICHT überschrieben (idempotent — mehrfaches Regulieren
am selben Tag hält das ECHTE Original fest).

Autonomie (settings.ai.autonomy):
  - 'auto'    → Anpassung direkt anwenden + persistieren (status='adjusted').
  - 'suggest' → Anpassung NICHT hart überschreiben; als Vorschlag markieren
                (status='suggested'), Originalplan bleibt aktiv. Die UI lässt das
                per Button bestätigen (→ apply_suggestion).

Erledigte Tage (status='done') werden NIE angefasst.
"""

from __future__ import annotations

import copy
import json
import logging
from datetime import date
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Konstanten ──────────────────────────────────────────────────────────────

# Wie stark Ziel-TSS/Dauer einer harten Radeinheit bei 🟡 gedrosselt werden.
YELLOW_CYCLING_REDUCTION = 0.25  # ~25 %

# Recovery-Vorgaben bei 🔴 (cycling → lockere Z1-Ausfahrt statt komplett Ruhe).
RED_RECOVERY_DURATION_MIN = 35  # innerhalb 30–45 min (KONZEPT §6.2)
RED_RECOVERY_ZONES = "Z1"
RED_RECOVERY_TSS = 20

# Status-Konstanten (siehe db.plan_days.status).
STATUS_DONE = "done"
STATUS_ADJUSTED = "adjusted"
STATUS_SUGGESTED = "suggested"

# Intensität, ab der eine Einheit als „hart" gilt.
_HARD = "hard"


# ════════════════════════════════════════════════════════════════════════════
#  REINE TRANSFORMATION (keine DB) — gut testbar
# ════════════════════════════════════════════════════════════════════════════

def _is_hard(workout: dict) -> bool:
    """True, wenn das Workout als harte Einheit gilt (intensity == 'hard')."""
    return (workout or {}).get("intensity") == _HARD


def _is_rest(workout: dict) -> bool:
    """True, wenn das Workout bereits ein Ruhetag ist."""
    return (workout or {}).get("sport") == "rest"


def _flag_summary(level: str, flags: list[str] | None) -> str:
    """Kurze „Warum"-Begründung inkl. der auslösenden Readiness-Flags."""
    label = {"yellow": "Tagesform gelb", "red": "Tagesform rot"}.get(level, level)
    if flags:
        return f"{label} ({', '.join(flags)})"
    return label


def _preserve_original(adjusted: dict, original: dict) -> None:
    """Sichert das unveränderte Original unter ``original`` (nur beim ersten Mal).

    Idempotent: liegt bereits ein ``original`` vor (mehrfaches Regulieren), bleibt
    das ECHTE Original erhalten — wir überschreiben es nicht mit einem bereits
    angepassten Workout.
    """
    if "original" not in adjusted:
        # Tiefe Kopie OHNE ein evtl. vorhandenes (verschachteltes) original-Feld.
        src = {k: v for k, v in original.items() if k != "original"}
        adjusted["original"] = copy.deepcopy(src)


def _adjust_cycling_yellow(workout: dict) -> tuple[dict, str] | None:
    """🟡 harte Radeinheit abschwächen: moderate + ~25 % weniger TSS/Dauer."""
    if not _is_hard(workout) or workout.get("sport") != "cycling":
        return None

    adjusted = copy.deepcopy(workout)
    _preserve_original(adjusted, workout)
    adjusted["intensity"] = "moderate"

    cyc = adjusted.get("cycling")
    if isinstance(cyc, dict):
        factor = 1.0 - YELLOW_CYCLING_REDUCTION
        if cyc.get("target_tss") is not None:
            cyc["target_tss"] = round(cyc["target_tss"] * factor)
        if cyc.get("duration_min") is not None:
            cyc["duration_min"] = round(cyc["duration_min"] * factor)
        old_target = cyc.get("target")
        if old_target:
            cyc["target"] = f"{old_target} (abgeschwächt: VO2max → Sweet Spot)"

    old_title = adjusted.get("title") or "Radeinheit"
    adjusted["title"] = f"{old_title} (abgeschwächt)"
    return adjusted, "harte Radeinheit auf Sweet Spot abgeschwächt"


def _adjust_strength_yellow(workout: dict) -> tuple[dict, str] | None:
    """🟡 Kraft abschwächen: Ziel-RIR jeder Übung +1; bei >2 Sätzen einen streichen.

    Greift auch bei nicht als 'hard' markierten Kraft-Einheiten, sofern Übungen
    vorhanden sind — Autoregulation per RIR ist hier die etablierte Methode.
    """
    if workout.get("sport") != "strength":
        return None
    strength = workout.get("strength")
    exercises = (strength or {}).get("exercises") if isinstance(strength, dict) else None
    if not exercises:
        return None

    adjusted = copy.deepcopy(workout)
    _preserve_original(adjusted, workout)
    for ex in adjusted["strength"]["exercises"]:
        if ex.get("rir") is not None:
            ex["rir"] = ex["rir"] + 1
        else:
            ex["rir"] = 1  # ohne Vorgabe: leichte Auto-Regulation ansetzen
        if ex.get("sets") is not None and ex["sets"] > 2:
            ex["sets"] = ex["sets"] - 1

    old_title = adjusted.get("title") or "Krafteinheit"
    adjusted["title"] = f"{old_title} (autoreguliert)"
    return adjusted, "Kraft autoreguliert (RIR +1, ein Satz weniger)"


def _adjust_yellow(workout: dict) -> tuple[dict | None, str | None]:
    """🟡-Logik: harte Rad-/jede Kraft-Einheit abschwächen, sonst unverändert."""
    result = _adjust_cycling_yellow(workout)
    if result is not None:
        return result
    result = _adjust_strength_yellow(workout)
    if result is not None:
        return result
    # cycling nicht-hart, easy/recovery, rest → keine Änderung bei gelb.
    return None, None


def _adjust_red(workout: dict) -> tuple[dict | None, str | None]:
    """🔴-Logik: harte Einheit → Recovery/Ruhe; leichte ggf. zu Ruhe.

    cycling (egal welche Intensität) → lockere Z1-Recovery-Ausfahrt (30–45 min).
    strength/other (hart oder mit Inhalt) → Ruhetag.
    Bereits leichte Einheiten ohne harte Vorgabe werden zu Ruhe.
    """
    if _is_rest(workout):
        return None, None  # schon Ruhe → nichts zu tun

    sport = workout.get("sport")
    adjusted = copy.deepcopy(workout)
    _preserve_original(adjusted, workout)

    if sport == "cycling":
        adjusted["intensity"] = "easy"
        adjusted["type"] = "recovery"
        adjusted["title"] = "Recovery-Ausfahrt (Z1)"
        adjusted["cycling"] = {
            "target": f"locker {RED_RECOVERY_ZONES} Recovery",
            "duration_min": RED_RECOVERY_DURATION_MIN,
            "target_tss": RED_RECOVERY_TSS,
            "zones": RED_RECOVERY_ZONES,
        }
        return adjusted, "harte Einheit in Recovery-Ausfahrt (Z1) gewandelt"

    # strength / other / sonst → Ruhetag.
    adjusted["sport"] = "rest"
    adjusted["intensity"] = "easy"
    adjusted["title"] = "Ruhetag"
    adjusted["type"] = "rest"
    adjusted.pop("strength", None)
    adjusted.pop("cycling", None)
    return adjusted, "Einheit zugunsten der Erholung in Ruhetag gewandelt"


def adjust_workout(workout: dict, level: str) -> tuple[dict | None, str | None]:
    """Reine Funktion: transformiert ein Workout-Dict gemäß Readiness-Ampel.

    Args:
        workout: das geplante Workout (geparstes plan_days-JSON, mit Feldern wie
            ``sport``, ``intensity``, ``cycling``/``strength``, ``title`` …).
        level: 'green' | 'yellow' | 'red' | 'unknown'.

    Returns:
        ``(angepasstes_workout, begruendung)`` bei einer Änderung, sonst
        ``(None, None)``. Das angepasste Workout enthält ``["original"]`` mit dem
        ursprünglichen Stand (nachvollziehbar/zurücknehmbar). Die Eingabe wird
        nie mutiert (deepcopy).

    Bei 🟢/❔ oder bereits passenden Einheiten gibt es keine Änderung.
    """
    if not isinstance(workout, dict) or not workout:
        return None, None

    if level == "yellow":
        return _adjust_yellow(workout)
    if level == "red":
        return _adjust_red(workout)
    # green / unknown / alles andere → keine Änderung (Plan unverändert).
    return None, None


# ════════════════════════════════════════════════════════════════════════════
#  DB-GETRIEBENE ORCHESTRIERUNG
# ════════════════════════════════════════════════════════════════════════════

def _today_iso() -> str:
    """Heutiges Datum als ISO-String (eigene Funktion → in Tests mockbar)."""
    return date.today().isoformat()


def _resolve_apply(apply: bool | None) -> bool:
    """Bestimmt, ob hart angewandt (True) oder nur vorgeschlagen (False) wird.

    ``apply`` überschreibt; ``None`` → aus settings.ai.autonomy ('auto'→True,
    'suggest'→False). Unbekannt/fehlend → konservativ 'suggest' (False).
    """
    if apply is not None:
        return apply
    from src.config import load_settings

    autonomy = (load_settings().get("ai", {}) or {}).get("autonomy", "suggest")
    return autonomy == "auto"


def _load_plan_day(conn, target_date: str) -> dict | None:
    """Lädt eine plan_days-Zeile als Dict mit geparstem ``workout`` (oder None)."""
    row = conn.execute(
        "SELECT * FROM plan_days WHERE datum = ?", (target_date,)
    ).fetchone()
    if row is None:
        return None
    data = dict(row)
    workout = None
    if data.get("geplantes_workout"):
        try:
            workout = json.loads(data["geplantes_workout"])
        except (json.JSONDecodeError, TypeError):
            workout = None
    data["workout"] = workout
    return data


def _summary(target_date, level, changed, status, reason, before, after) -> dict:
    """Baut das einheitliche Rückgabe-Dict."""
    return {
        "date": target_date,
        "level": level,
        "changed": changed,
        "status": status,
        "reason": reason,
        "before": before,
        "after": after,
    }


def autoregulate_day(
    target_date: str | None = None,
    db_path: str | Path | None = None,
    apply: bool | None = None,
) -> dict:
    """Reguliert die geplante Einheit eines Tages anhand der Readiness-Ampel.

    Lädt Readiness + plan_day, ruft ``adjust_workout``, respektiert die Autonomie
    und schreibt — bei 'auto' — die Anpassung in ``plan_days`` (status='adjusted',
    ``anpassungsgrund``, Original gesichert) bzw. markiert sie bei 'suggest'
    (status='suggested', Originalplan bleibt aktiv).

    Graceful in jedem Randfall: kein plan_day, status='done', kein angepasstes
    Workout (green/unknown) → ``changed=False``, nichts wird geschrieben.

    Args:
        target_date: ISO-Datum (Default: heute).
        db_path: optionaler DB-Pfad (Tests).
        apply: True=anwenden, False=nur vorschlagen, None=aus settings.ai.autonomy.

    Returns:
        ``{date, level, changed, status, reason, before, after}``.
    """
    from src.core.db import get_connection, init_db
    from src.core.readiness import readiness_for_date

    init_db(db_path)
    target_date = target_date or _today_iso()

    readiness = readiness_for_date(target_date, db_path=db_path)
    level = readiness.get("level", "unknown")

    conn = get_connection(db_path)
    try:
        plan_day = _load_plan_day(conn, target_date)

        # — Graceful: kein Plan-Tag oder leeres Workout → nichts zu tun.
        if plan_day is None or not plan_day.get("workout"):
            logger.info("Auto-Regulation %s: kein Plan-Tag/Workout → keine Änderung.",
                        target_date)
            return _summary(target_date, level, False, None, None, None, None)

        # — Erledigte Tage NIE anfassen.
        if plan_day.get("status") == STATUS_DONE:
            logger.info("Auto-Regulation %s: Tag ist 'done' → unangetastet.", target_date)
            return _summary(target_date, level, False, STATUS_DONE, None,
                            plan_day["workout"], None)

        before = plan_day["workout"]
        adjusted, base_reason = adjust_workout(before, level)

        # — green/unknown/keine Änderung nötig.
        if adjusted is None:
            logger.info("Auto-Regulation %s: Ampel '%s' → Plan unverändert.",
                        target_date, level)
            return _summary(target_date, level, False, plan_day.get("status"),
                            None, before, None)

        reason = f"{base_reason} — {_flag_summary(level, readiness.get('flags'))}"
        do_apply = _resolve_apply(apply)
        status = STATUS_ADJUSTED if do_apply else STATUS_SUGGESTED

        if do_apply:
            # 'auto': Anpassung hart anwenden — angepasstes Workout wird aktiv.
            conn.execute(
                """
                UPDATE plan_days
                SET geplantes_workout = ?, status = ?, anpassungsgrund = ?
                WHERE datum = ?
                """,
                (json.dumps(adjusted, ensure_ascii=False), STATUS_ADJUSTED,
                 reason, target_date),
            )
        else:
            # 'suggest': Originalplan bleibt aktiv (geplantes_workout unverändert);
            # der Vorschlag wird separat unter "suggestion" gesichert, status markiert.
            stored = copy.deepcopy(before)
            stored["suggestion"] = adjusted
            conn.execute(
                """
                UPDATE plan_days
                SET geplantes_workout = ?, status = ?, anpassungsgrund = ?
                WHERE datum = ?
                """,
                (json.dumps(stored, ensure_ascii=False), STATUS_SUGGESTED,
                 reason, target_date),
            )
        conn.commit()
        logger.info("Auto-Regulation %s: Ampel '%s' → %s (%s).",
                    target_date, level, status, reason)
        return _summary(target_date, level, True, status, reason, before, adjusted)
    finally:
        conn.close()


def apply_suggestion(
    target_date: str,
    db_path: str | Path | None = None,
) -> dict:
    """Macht aus einem 'suggested' Plan-Tag ein angewandtes 'adjusted'.

    Für den Bestätigen-Button im 'suggest'-Modus: der zuvor unter
    ``workout["suggestion"]`` hinterlegte Vorschlag wird zum aktiven Workout
    befördert (status='adjusted'), das Original bleibt darin gesichert.

    Graceful: kein Tag / kein Vorschlag / kein 'suggested'-Status → ``changed=False``.

    Returns:
        ``{date, level, changed, status, reason, before, after}`` (level None — hier
        irrelevant; die Begründung bleibt erhalten).
    """
    from src.core.db import get_connection, init_db

    init_db(db_path)
    conn = get_connection(db_path)
    try:
        plan_day = _load_plan_day(conn, target_date)
        if plan_day is None or not plan_day.get("workout"):
            return _summary(target_date, None, False, None, None, None, None)

        if plan_day.get("status") != STATUS_SUGGESTED:
            return _summary(target_date, None, False, plan_day.get("status"),
                            None, plan_day["workout"], None)

        stored = plan_day["workout"]
        suggestion = stored.get("suggestion")
        if not suggestion:
            # Markiert als suggested, aber kein Vorschlag hinterlegt → nichts zu tun.
            return _summary(target_date, None, False, STATUS_SUGGESTED, None,
                            stored, None)

        adjusted = copy.deepcopy(suggestion)
        reason = plan_day.get("anpassungsgrund")
        conn.execute(
            """
            UPDATE plan_days
            SET geplantes_workout = ?, status = ?
            WHERE datum = ?
            """,
            (json.dumps(adjusted, ensure_ascii=False), STATUS_ADJUSTED, target_date),
        )
        conn.commit()
        logger.info("Vorschlag bestätigt %s → 'adjusted'.", target_date)
        return _summary(target_date, None, True, STATUS_ADJUSTED, reason,
                        stored, adjusted)
    finally:
        conn.close()
