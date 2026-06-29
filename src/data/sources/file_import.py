"""Datei-Import-Adapter (FIT & GPX) für Sirdar — KONZEPT §7, Phase 1B.

Parst lokale Aktivitätsdateien in ``WorkoutDict``s (siehe base.py):

FIT (via ``fitparse``):
    - Aktivitätsdatum, Sportart (aus ``sport``-Message abgeleitet), Dauer, Distanz,
      Ø-HF aus der ``session``-Message bzw. ersatzweise aus den ``record``-Samples.
    - Power-Samples aus den ``record``-Messages → NP/IF/TSS via ``core.metrics``
      (FTP aus dem Profil). Ohne Power: np/if_/tss = None.

GPX (via ``gpxpy``):
    - Aktivitätsdatum aus dem ersten Trackpunkt-Timestamp, Dauer aus
      First/Last-Timestamp, Distanz aus ``gpx.length_3d()``.
    - Ø-HF aus den Garmin-TrackPointExtensions (``<gpxtpx:hr>``), falls vorhanden.
    - GPX trägt i. d. R. keine Power → np/if_/tss = None.

Designprinzip (KONZEPT §6.3): keine Werte erfinden. Fehlt Power/FTP, bleiben die
Power-Kennzahlen ``None`` statt geschätzt zu werden.
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

from src.core.metrics import power_metrics
from src.data.sources.base import WorkoutDict

logger = logging.getLogger(__name__)

SOURCE_NAME = "file_import"

# FIT-``sport``-Enum (fitparse liefert den String) → unsere ``sportart``-Werte.
_FIT_SPORT_MAP = {
    "cycling": "cycling",
    "running": "running",
    "swimming": "swimming",
    "walking": "walking",
    "hiking": "walking",
    "training": "strength",
    "fitness_equipment": "strength",
    "rowing": "rowing",
}

# Namespaces der Garmin-TrackPointExtension (für Herzfrequenz in GPX).
_GPX_TPX_NS = {
    "tpx1": "http://www.garmin.com/xmlschemas/TrackPointExtension/v1",
    "tpx2": "http://www.garmin.com/xmlschemas/TrackPointExtension/v2",
}


# ─── Helfer ──────────────────────────────────────────────────────────────

def _iso_date(dt: datetime | None) -> str | None:
    """datetime → 'YYYY-MM-DD' (UTC-naiv behandelt). None bleibt None."""
    if dt is None:
        return None
    return dt.date().isoformat()


def _round_or_none(value, ndigits: int):
    return round(value, ndigits) if value is not None else None


# ─── FIT ─────────────────────────────────────────────────────────────────

def parse_fit(path: str | Path, ftp: int | None = None) -> WorkoutDict:
    """Parst eine ``.fit``-Datei zu einem WorkoutDict.

    ``ftp`` (Watt) wird für IF/TSS benötigt; ohne FTP oder ohne Power bleiben
    np/if_/tss = None.
    """
    import fitparse

    path = Path(path)
    fitfile = fitparse.FitFile(str(path))

    powers: list[float] = []
    hrs: list[int] = []
    timestamps: list[datetime] = []

    # Aggregat-Werte bevorzugt aus der session-Message (zuverlässiger als Samples).
    session: dict = {}
    sport_raw: str | None = None
    sub_sport_raw: str | None = None

    for record in fitfile.get_messages():
        name = record.name
        if name == "record":
            values = record.get_values()
            p = values.get("power")
            if p is not None:
                powers.append(float(p))
            hr = values.get("heart_rate")
            if hr is not None:
                hrs.append(int(hr))
            ts = values.get("timestamp")
            if isinstance(ts, datetime):
                timestamps.append(ts)
        elif name == "session":
            session = record.get_values()
            sport_raw = sport_raw or session.get("sport")
            sub_sport_raw = sub_sport_raw or session.get("sub_sport")
        elif name == "sport":
            values = record.get_values()
            sport_raw = sport_raw or values.get("sport")
            sub_sport_raw = sub_sport_raw or values.get("sub_sport")

    # — Sportart ableiten —
    sportart = _FIT_SPORT_MAP.get(str(sport_raw).lower() if sport_raw else "", "cycling")

    # — Datum: session.start_time bevorzugt, sonst erstes Sample —
    start = session.get("start_time")
    if not isinstance(start, datetime) and timestamps:
        start = min(timestamps)
    datum = _iso_date(start if isinstance(start, datetime) else None)

    # — Dauer (Minuten): session.total_timer_time (Sek.) bevorzugt —
    duration_s = session.get("total_timer_time") or session.get("total_elapsed_time")
    if not duration_s and len(timestamps) >= 2:
        duration_s = (max(timestamps) - min(timestamps)).total_seconds()
    dauer = _round_or_none(duration_s / 60.0, 1) if duration_s else None

    # — Distanz (km): session.total_distance ist in Metern —
    total_distance_m = session.get("total_distance")
    distanz = _round_or_none(total_distance_m / 1000.0, 2) if total_distance_m else None

    # — Ø-HF: session.avg_heart_rate bevorzugt, sonst Mittel der Samples —
    avg_hr = session.get("avg_heart_rate")
    if avg_hr is None and hrs:
        avg_hr = round(sum(hrs) / len(hrs))
    avg_hr = int(avg_hr) if avg_hr is not None else None

    # — Power-Kennzahlen (FIT-records sind i. d. R. 1 Hz) —
    # Dauer für TSS: bevorzugt die tatsächliche Bewegungszeit in Sekunden.
    tss_duration_s = duration_s if duration_s else (len(powers) if powers else None)
    metrics = power_metrics(powers, tss_duration_s, ftp, sample_rate_s=1)

    return WorkoutDict(
        datum=datum,
        sportart=sportart,
        typ=None,
        dauer=dauer,
        distanz=distanz,
        np=metrics["np"],
        if_=metrics["if_"],
        tss=metrics["tss"],
        avg_hr=avg_hr,
        quelle=SOURCE_NAME,
        raw_ref=path.name,
        notiz=None,
    )


# ─── GPX ─────────────────────────────────────────────────────────────────

def _gpx_point_hr(point) -> int | None:
    """Liest die Herzfrequenz aus den Garmin-TrackPointExtensions eines Punkts."""
    for ext in getattr(point, "extensions", []) or []:
        # ext ist ein xml.etree.ElementTree.Element (gpxpy speichert sie roh).
        for elem in ext.iter():
            tag = elem.tag
            # Tag kann '{ns}hr' oder schlicht 'hr' sein.
            local = tag.rsplit("}", 1)[-1].lower()
            if local == "hr" and elem.text:
                try:
                    return int(float(elem.text))
                except (ValueError, TypeError):
                    return None
    return None


def parse_gpx(path: str | Path) -> WorkoutDict:
    """Parst eine ``.gpx``-Datei zu einem WorkoutDict.

    GPX trägt selten Power → np/if_/tss bleiben None. HF wird, falls als
    Garmin-Extension vorhanden, gemittelt.
    """
    import gpxpy

    path = Path(path)
    with open(path, encoding="utf-8") as fh:
        gpx = gpxpy.parse(fh)

    # — Sportart aus dem ersten Track-Type, falls gesetzt (sonst cycling-Default) —
    sportart = "cycling"
    for track in gpx.tracks:
        if track.type:
            sportart = _FIT_SPORT_MAP.get(track.type.lower(), track.type.lower())
            break

    # — Zeitpunkte + HF über alle Punkte sammeln —
    times: list[datetime] = []
    hrs: list[int] = []
    for track in gpx.tracks:
        for segment in track.segments:
            for point in segment.points:
                if point.time:
                    times.append(point.time)
                hr = _gpx_point_hr(point)
                if hr is not None:
                    hrs.append(hr)

    # — Datum + Dauer —
    datum = None
    duration_s = None
    if times:
        times.sort()
        datum = _iso_date(times[0])
        duration_s = (times[-1] - times[0]).total_seconds()
    dauer = _round_or_none(duration_s / 60.0, 1) if duration_s else None

    # — Distanz (km): gpxpy length_3d() liefert Meter —
    length_m = gpx.length_3d() or gpx.length_2d()
    distanz = _round_or_none(length_m / 1000.0, 2) if length_m else None

    avg_hr = round(sum(hrs) / len(hrs)) if hrs else None

    return WorkoutDict(
        datum=datum,
        sportart=sportart,
        typ=None,
        dauer=dauer,
        distanz=distanz,
        np=None,
        if_=None,
        tss=None,
        avg_hr=avg_hr,
        quelle=SOURCE_NAME,
        raw_ref=path.name,
        notiz=None,
    )


# ─── Dispatch + Source-Klasse (erfüllt WorkoutSource-Protocol) ───────────

def parse_workout_file(path: str | Path, ftp: int | None = None) -> WorkoutDict:
    """Dispatcht anhand der Dateiendung auf parse_fit / parse_gpx.

    Raises ``ValueError`` bei unbekannter Endung.
    """
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".fit":
        return parse_fit(path, ftp=ftp)
    if suffix == ".gpx":
        return parse_gpx(path)
    raise ValueError(f"Nicht unterstützte Dateiendung: {suffix!r} ({path.name})")


class FileImportSource:
    """``WorkoutSource``-Adapter für lokale FIT/GPX-Dateien.

    ``parse(path)`` gibt eine Liste mit genau einem WorkoutDict zurück (eine Datei
    = eine Aktivität), damit das Interface zu Multi-Aktivitäts-Quellen (Garmin/
    Strava) konsistent bleibt.
    """

    name = SOURCE_NAME

    def __init__(self, ftp: int | None = None) -> None:
        self.ftp = ftp

    def parse(self, source: str) -> list[WorkoutDict]:
        return [parse_workout_file(source, ftp=self.ftp)]
