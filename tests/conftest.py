"""Gemeinsame Test-Fixtures.

Schützt das Repo davor, beim Testlauf eine echte ``training.db`` anzulegen:
Routen wie ``GET /import`` rufen ``recent_workouts()`` ohne expliziten db_path und
würden sonst ``db.DB_PATH`` (Repo-Root/training.db) erzeugen. Die autouse-Session-
Fixture biegt ``DB_PATH`` einmalig auf eine temporäre Datei um.
"""

from __future__ import annotations

import pytest

from src.core import db


@pytest.fixture(autouse=True, scope="session")
def _redirect_db_path(tmp_path_factory):
    """Leitet db.DB_PATH für die gesamte Test-Session auf eine temp-Datei um."""
    tmp_db = tmp_path_factory.mktemp("sirdar_db") / "training.db"
    original = db.DB_PATH
    db.DB_PATH = tmp_db
    yield
    db.DB_PATH = original
