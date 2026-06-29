"""Kalender-Datenquelle (CalDAV / .ics) für Sirdar — KONZEPT §4/§7, Phase 2A.

Liefert kommende Termine im Planungsfenster, damit der Coach Einheiten in die
freie Zeit legen kann (KONZEPT §6: „An Realität anpassen — freie Slots aus
Kalender"). KEINE komplexe Slot-Berechnung — nur eine grobe Tagesbelegung.

Zwei Modi, beide über ``settings.integrations.calendar_caldav.url`` gesteuert:
  1. CalDAV (caldav-Lib) — wenn ``username``/``password`` gesetzt sind: verbindet
     sich, listet Kalender und sucht Events im Zeitfenster.
  2. Reine .ics-URL — wenn nur ``url`` (ohne user/pass) gesetzt ist: httpx-GET der
     .ics-Datei + Parsing via ``icalendar``.

Designprinzip (KONZEPT §6.3): keine Werte erfinden. Fehlt der Kalender (deaktiviert,
keine URL, Netz-/Auth-Fehler), liefert die Quelle ``[]`` — der Coach plant dann
ohne Termine weiter (Plan-Generierung darf NIE am Kalender scheitern).
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta
from typing import Any

import httpx

from src.config import load_settings

logger = logging.getLogger(__name__)

SOURCE_NAME = "calendar_caldav"

_DEFAULT_DAYS = 14
_HTTP_TIMEOUT_S = 15.0


# ─── Helfer ──────────────────────────────────────────────────────────────

def _as_date(value: Any) -> date | None:
    """datetime/date/None → date (für Fenster-Vergleiche; tz-naiv behandelt)."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return None


def _iso_time(value: Any) -> str | None:
    """datetime → 'HH:MM'. Bei reinem date (Ganztag) → None."""
    if isinstance(value, datetime):
        return value.strftime("%H:%M")
    return None


def _is_all_day(dtstart: Any) -> bool:
    """Ein Termin ist ganztägig, wenn DTSTART ein reines ``date`` (kein datetime) ist."""
    return isinstance(dtstart, date) and not isinstance(dtstart, datetime)


def _event_duration_hours(dtstart: Any, dtend: Any) -> float:
    """Grobe Dauer in Stunden (für busy_by_day). Ganztag → 0 (zählt separat)."""
    if isinstance(dtstart, datetime) and isinstance(dtend, datetime):
        delta = (dtend - dtstart).total_seconds() / 3600.0
        return round(max(0.0, delta), 2)
    return 0.0


def _normalize_event(dtstart: Any, dtend: Any, summary: Any) -> dict | None:
    """Baut ein kompaktes Event-Dict aus rohen icalendar-Werten. None bei kaputtem Event."""
    start_date = _as_date(dtstart)
    if start_date is None:
        return None
    all_day = _is_all_day(dtstart)
    return {
        "date": start_date.isoformat(),
        "start": None if all_day else _iso_time(dtstart),
        "end": None if all_day else _iso_time(dtend),
        "summary": (str(summary).strip() if summary is not None else None) or None,
        "all_day": all_day,
        "_duration_h": _event_duration_hours(dtstart, dtend),
    }


def _within_window(ev_date: date, start_date: date, end_date: date) -> bool:
    return start_date <= ev_date <= end_date


# ─── .ics-Parsing (httpx GET + icalendar) ─────────────────────────────────

def _parse_ics(ics_text: str, start_date: date, end_date: date) -> list[dict]:
    """Parst einen .ics-String und filtert VEVENTs auf das [start, end]-Fenster."""
    import icalendar

    cal = icalendar.Calendar.from_ical(ics_text)
    events: list[dict] = []
    for comp in cal.walk("VEVENT"):
        dtstart_prop = comp.get("dtstart")
        if dtstart_prop is None:
            continue
        dtstart = dtstart_prop.dt
        dtend_prop = comp.get("dtend")
        dtend = dtend_prop.dt if dtend_prop is not None else None

        ev = _normalize_event(dtstart, dtend, comp.get("summary"))
        if ev is None:
            continue
        if _within_window(date.fromisoformat(ev["date"]), start_date, end_date):
            events.append(ev)
    return events


def _fetch_ics_events(url: str, start_date: date, end_date: date) -> list[dict]:
    """Lädt eine .ics-Datei via httpx und parst die Events im Fenster."""
    # webcal:// ist ein iCal-Alias für https.
    if url.startswith("webcal://"):
        url = "https://" + url[len("webcal://"):]
    try:
        response = httpx.get(url, timeout=_HTTP_TIMEOUT_S, follow_redirects=True)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning("ICS-Download fehlgeschlagen (%s): %s", url, exc)
        return []
    try:
        return _parse_ics(response.text, start_date, end_date)
    except Exception:  # noqa: BLE001 — Parsing darf den Coach nie crashen
        logger.warning("ICS-Parsing fehlgeschlagen.", exc_info=True)
        return []


# ─── CalDAV-Abfrage (caldav-Lib) ──────────────────────────────────────────

def _fetch_caldav_events(
    url: str,
    username: str | None,
    password: str | None,
    start_date: date,
    end_date: date,
) -> list[dict]:
    """Verbindet sich per CalDAV, durchsucht alle Kalender im Fenster und parst Events."""
    import caldav

    # Suchfenster als datetime (caldav.search erwartet datetime-Grenzen).
    start_dt = datetime.combine(start_date, time.min)
    end_dt = datetime.combine(end_date + timedelta(days=1), time.min)

    events: list[dict] = []
    try:
        with caldav.DAVClient(url=url, username=username, password=password) as client:
            principal = client.principal()
            calendars = principal.calendars()
            for calendar in calendars:
                try:
                    found = calendar.search(
                        event=True, start=start_dt, end=end_dt, expand=True
                    )
                except Exception:  # noqa: BLE001 — ein Kalender darf nicht alles kippen
                    logger.warning("CalDAV-Suche in einem Kalender fehlgeschlagen.", exc_info=True)
                    continue
                for obj in found:
                    events.extend(_events_from_caldav_obj(obj, start_date, end_date))
    except Exception:  # noqa: BLE001 — Verbindung/Auth-Fehler robust behandeln
        logger.warning("CalDAV-Verbindung/Abfrage fehlgeschlagen (%s).", url, exc_info=True)
        return []
    return events


def _events_from_caldav_obj(obj: Any, start_date: date, end_date: date) -> list[dict]:
    """Extrahiert die VEVENTs eines caldav-CalendarObjectResource (über dessen .icalendar_instance)."""
    out: list[dict] = []
    try:
        ical = obj.icalendar_instance
    except Exception:  # noqa: BLE001
        return out
    for comp in ical.walk("VEVENT"):
        dtstart_prop = comp.get("dtstart")
        if dtstart_prop is None:
            continue
        dtend_prop = comp.get("dtend")
        ev = _normalize_event(
            dtstart_prop.dt,
            dtend_prop.dt if dtend_prop is not None else None,
            comp.get("summary"),
        )
        if ev is None:
            continue
        if _within_window(date.fromisoformat(ev["date"]), start_date, end_date):
            out.append(ev)
    return out


# ─── Öffentliche API ─────────────────────────────────────────────────────

def get_events(
    start_date: date | str | None = None,
    days: int = _DEFAULT_DAYS,
    url: str | None = None,
    username: str | None = None,
    password: str | None = None,
) -> list[dict]:
    """Lädt Kalender-Events im Fenster [start_date, start_date+days-1].

    Wählt den Modus automatisch: mit ``username``/``password`` → CalDAV, sonst
    (nur ``url``) → reine .ics-URL. Liefert eine Liste kompakter Event-Dicts
    ``{date, start, end, summary, all_day}`` (chronologisch sortiert). Bei Fehler
    oder fehlender URL → ``[]`` (wirft NIE).
    """
    if not url:
        logger.info("Kalender: keine URL — keine Events.")
        return []

    if isinstance(start_date, str):
        start = date.fromisoformat(start_date)
    elif isinstance(start_date, date):
        start = start_date
    else:
        start = date.today()
    end = start + timedelta(days=max(1, int(days or _DEFAULT_DAYS)) - 1)

    if username and password:
        events = _fetch_caldav_events(url, username, password, start, end)
    else:
        events = _fetch_ics_events(url, start, end)

    # Chronologisch sortieren (Ganztag zuerst, dann nach Startzeit).
    events.sort(key=lambda e: (e["date"], e.get("start") or ""))
    return events


def busy_by_day(events: list[dict]) -> dict[str, dict]:
    """Grobe Tagesbelegung je Datum (der Coach legt Einheiten in freie Zeit).

    Returns:
        Dict ``{ "YYYY-MM-DD": {"events": [summary,...], "busy_hours": float,
        "all_day": bool} }``. ``busy_hours`` summiert die (groben) Termin-Dauern;
        Ganztags-Termine setzen ``all_day=True`` (zählen nicht in busy_hours, sind
        aber ein starkes Signal, dass der Tag belegt ist).
    """
    result: dict[str, dict] = {}
    for ev in events:
        day = ev["date"]
        bucket = result.setdefault(day, {"events": [], "busy_hours": 0.0, "all_day": False})
        if ev.get("summary"):
            bucket["events"].append(ev["summary"])
        bucket["busy_hours"] = round(bucket["busy_hours"] + float(ev.get("_duration_h", 0.0)), 2)
        if ev.get("all_day"):
            bucket["all_day"] = True
    return result


def _strip_internal(events: list[dict]) -> list[dict]:
    """Entfernt interne ``_``-Felder (z. B. _duration_h) für den Coach-Kontext."""
    return [{k: v for k, v in ev.items() if not k.startswith("_")} for ev in events]


def events_from_settings(
    start_date: date | str | None = None,
    days: int = _DEFAULT_DAYS,
) -> list[dict]:
    """Zieht die Config aus settings.integrations.calendar_caldav und ruft get_events.

    Deaktiviert oder ohne URL → ``[]`` (kein Fehler). Liefert Events OHNE interne
    Felder (kontext-fertig).
    """
    cfg = (load_settings().get("integrations", {}) or {}).get("calendar_caldav", {}) or {}
    if not cfg.get("enabled"):
        logger.info("calendar_caldav deaktiviert — keine Events.")
        return []
    url = cfg.get("url")
    if not url:
        logger.info("calendar_caldav aktiv, aber keine URL gesetzt — keine Events.")
        return []
    events = get_events(
        start_date=start_date,
        days=days,
        url=url,
        username=cfg.get("username") or None,
        password=cfg.get("password") or None,
    )
    return _strip_internal(events)


# ─── Source-Klasse (analog FileImportSource; konsistentes Adapter-Muster) ──

class CalendarSource:
    """Adapter für CalDAV/.ics (Kalender-Datenquelle).

    Wie ``WeatherSource`` Kontextdaten (keine Aktivitäten), folgt aber demselben
    Stil (Modul-Funktionen + dünne Klasse) wie ``FileImportSource``.
    """

    name = SOURCE_NAME

    def __init__(
        self,
        url: str | None = None,
        username: str | None = None,
        password: str | None = None,
    ) -> None:
        self.url = url
        self.username = username
        self.password = password

    def fetch(self, start_date: date | str | None = None, days: int = _DEFAULT_DAYS) -> list[dict]:
        if not self.url:
            return events_from_settings(start_date=start_date, days=days)
        events = get_events(
            start_date=start_date,
            days=days,
            url=self.url,
            username=self.username,
            password=self.password,
        )
        return _strip_internal(events)
