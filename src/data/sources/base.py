"""Gemeinsames Interface für Trainings-Datenquellen (Adapter-Pattern, KONZEPT §7).

Eine Datenquelle (Datei-Import, Garmin, Strava, …) liefert geparste Aktivitäten
als ``WorkoutDict`` — ein flaches Dict, dessen Schlüssel exakt den Spalten der
``workouts``-Tabelle entsprechen (KONZEPT §5 / src/core/db.py). So kann der
Persistenz-Layer (``store.import_*``) jede Quelle gleich behandeln.

Garmin/Strava-Adapter implementieren später dasselbe ``WorkoutSource``-Protocol
und füllen denselben ``WorkoutDict`` — der Rest der Pipeline bleibt unverändert.
"""

from __future__ import annotations

from typing import Protocol, TypedDict, runtime_checkable


class WorkoutDict(TypedDict, total=False):
    """Eine geparste Aktivität — Schlüssel = Spalten der ``workouts``-Tabelle.

    Pflicht (für sinnvolle Persistenz): ``datum``, ``sportart``, ``quelle``.
    Alle übrigen Felder sind optional und ``None``, wenn die Quelle sie nicht
    liefert (z. B. ``np``/``if_``/``tss`` ohne Powermeter).
    """

    datum: str          # ISO-Datum (YYYY-MM-DD)
    sportart: str       # cycling | running | strength | ...
    typ: str | None     # frei (z. B. Aktivitätsname); Plan-Typ kommt später
    dauer: float | None  # Minuten
    distanz: float | None  # km
    np: float | None    # Normalized Power (Watt)
    if_: float | None   # Intensity Factor
    tss: float | None   # Training Stress Score
    avg_hr: int | None  # Ø-Herzfrequenz
    quelle: str         # file_import | garmin | strava | manual
    raw_ref: str | None  # Referenz auf Rohdatei (Dateiname)
    notiz: str | None


@runtime_checkable
class WorkoutSource(Protocol):
    """Protocol, das jede Trainings-Datenquelle erfüllen muss.

    Beispiel-Implementierung: ``src/data/sources/file_import.FileImportSource``.
    Künftige Quellen (Garmin/Strava) implementieren dasselbe ``parse``-Interface,
    ggf. mit anderer Eingabe (z. B. API-Range statt Dateipfad).
    """

    #: Kurzname der Quelle — landet in ``workouts.quelle``.
    name: str

    def parse(self, source: str) -> list[WorkoutDict]:
        """Liest ``source`` (z. B. ein Dateipfad) und gibt geparste Workouts zurück."""
        ...
