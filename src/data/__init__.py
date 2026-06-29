"""Datenquellen-Schicht für Sirdar (Adapter-/Plug-in-Pattern, KONZEPT §7).

Jede Trainings-/Health-Datenquelle (Datei-Import, Garmin, Strava, …) liegt als
eigenständiges Modul unter ``src/data/sources/`` und erfüllt das gemeinsame
``WorkoutSource``-Interface (siehe ``src/data/sources/base.py``). So lassen sich
Quellen frei kombinieren und in ``settings.json`` aktivieren.
"""

from __future__ import annotations
