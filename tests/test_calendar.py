"""Phase-2A Tests: Kalender-Datenquelle (src/data/sources/calendar_caldav.py).

KEINE echten Netz-/CalDAV-Calls: für die .ics-URL wird ``httpx.get`` gemonkeypatcht
und liefert einen kleinen Inline-ICS-String; die Parse-/busy_by_day-Logik wird direkt
getestet. disabled/keine URL → []; Netzfehler → [] (nie Crash).
"""

from __future__ import annotations

from datetime import date

import httpx
import pytest

from src.data.sources import calendar_caldav as cal


# Kleiner Inline-Kalender: ein zeitlich begrenzter Termin + ein Ganztags-Termin
# + ein Termin außerhalb des Fensters (darf nicht auftauchen).
INLINE_ICS = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//sirdar-test//EN
BEGIN:VEVENT
UID:1@test
DTSTART:20260629T090000Z
DTEND:20260629T103000Z
SUMMARY:Standup Meeting
END:VEVENT
BEGIN:VEVENT
UID:2@test
DTSTART;VALUE=DATE:20260630
DTEND;VALUE=DATE:20260701
SUMMARY:Urlaubstag
END:VEVENT
BEGIN:VEVENT
UID:3@test
DTSTART:20260805T090000Z
DTEND:20260805T100000Z
SUMMARY:Weit in der Zukunft
END:VEVENT
END:VCALENDAR
"""


class _FakeResponse:
    def __init__(self, text, status=200):
        self.text = text
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("err", request=None, response=None)


@pytest.fixture()
def mock_ics(monkeypatch):
    """Patcht httpx.get auf den Inline-ICS-String."""
    def fake_get(url, timeout=None, follow_redirects=None, **kw):
        return _FakeResponse(INLINE_ICS)

    monkeypatch.setattr(cal.httpx, "get", fake_get)


# ─── .ics-Parsing über get_events (URL ohne user/pass) ─────────────────────

def test_get_events_parses_ics_window(mock_ics):
    events = cal.get_events(
        start_date=date(2026, 6, 29), days=7, url="https://example.com/cal.ics"
    )
    # 2 Events im Fenster (29./30.6.), der 5.8. fällt raus.
    assert len(events) == 2
    summaries = [e["summary"] for e in events]
    assert "Standup Meeting" in summaries
    assert "Urlaubstag" in summaries
    assert "Weit in der Zukunft" not in summaries


def test_get_events_timed_event_fields(mock_ics):
    events = cal.get_events(
        start_date=date(2026, 6, 29), days=7, url="https://example.com/cal.ics"
    )
    standup = next(e for e in events if e["summary"] == "Standup Meeting")
    assert standup["date"] == "2026-06-29"
    assert standup["all_day"] is False
    assert standup["start"] == "09:00"
    assert standup["end"] == "10:30"


def test_get_events_all_day_event_fields(mock_ics):
    events = cal.get_events(
        start_date=date(2026, 6, 29), days=7, url="https://example.com/cal.ics"
    )
    urlaub = next(e for e in events if e["summary"] == "Urlaubstag")
    assert urlaub["date"] == "2026-06-30"
    assert urlaub["all_day"] is True
    assert urlaub["start"] is None
    assert urlaub["end"] is None


def test_get_events_accepts_iso_string_start(mock_ics):
    events = cal.get_events(
        start_date="2026-06-29", days=7, url="https://example.com/cal.ics"
    )
    assert len(events) == 2


# ─── busy_by_day ───────────────────────────────────────────────────────────

def test_busy_by_day(mock_ics):
    events = cal.get_events(
        start_date=date(2026, 6, 29), days=7, url="https://example.com/cal.ics"
    )
    busy = cal.busy_by_day(events)
    assert set(busy.keys()) == {"2026-06-29", "2026-06-30"}

    # Standup: 1,5 h belegt, nicht ganztags.
    day1 = busy["2026-06-29"]
    assert day1["busy_hours"] == 1.5
    assert day1["all_day"] is False
    assert "Standup Meeting" in day1["events"]

    # Urlaubstag: ganztags, 0 busy_hours (zählt über all_day-Flag).
    day2 = busy["2026-06-30"]
    assert day2["all_day"] is True
    assert day2["busy_hours"] == 0.0
    assert "Urlaubstag" in day2["events"]


def test_busy_by_day_empty():
    assert cal.busy_by_day([]) == {}


# ─── Robustheit ────────────────────────────────────────────────────────────

def test_get_events_no_url_returns_empty():
    assert cal.get_events(start_date=date(2026, 6, 29), days=7, url=None) == []
    assert cal.get_events(start_date=date(2026, 6, 29), days=7, url="") == []


def test_get_events_network_error_returns_empty(monkeypatch):
    def boom(*a, **kw):
        raise httpx.ConnectError("no network")

    monkeypatch.setattr(cal.httpx, "get", boom)
    assert cal.get_events(url="https://example.com/cal.ics") == []


def test_get_events_bad_ics_returns_empty(monkeypatch):
    def fake_get(*a, **kw):
        return _FakeResponse("THIS IS NOT ICS")

    monkeypatch.setattr(cal.httpx, "get", fake_get)
    # Kaputtes ICS → leere Liste, kein Crash.
    assert cal.get_events(url="https://example.com/cal.ics") == []


# ─── events_from_settings (Config-gesteuert) ───────────────────────────────

def _patch_settings(monkeypatch, cfg):
    monkeypatch.setattr(
        cal, "load_settings",
        lambda: {"integrations": {"calendar_caldav": cfg}},
    )


def test_events_from_settings_disabled_returns_empty(monkeypatch):
    _patch_settings(monkeypatch, {"enabled": False, "url": "https://example.com/cal.ics"})
    assert cal.events_from_settings() == []


def test_events_from_settings_no_url_returns_empty(monkeypatch):
    _patch_settings(monkeypatch, {"enabled": True, "url": ""})
    assert cal.events_from_settings() == []


def test_events_from_settings_ics_strips_internal(monkeypatch, mock_ics):
    _patch_settings(monkeypatch, {"enabled": True, "url": "https://example.com/cal.ics"})
    events = cal.events_from_settings(start_date="2026-06-29", days=7)
    assert len(events) == 2
    # interne Felder (_duration_h) sind im Kontext-fertigen Output entfernt.
    assert all(not k.startswith("_") for e in events for k in e)
