"""Phase-1B Tests: Datei-Import (GPX) + Persistenz + Web-Upload-Seite.

GPX-Test ist Pflicht (synthetischer Inline-String). FIT-Unit-Tests werden
übersprungen: fitparse kann nur echte (binäre, CRC-geprüfte) .fit-Dateien lesen,
ein einfacher Inline-Fixture ist nicht trivial erzeugbar. Die FIT-Parselogik
(parse_fit) teilt sich die Metrik-Pipeline mit den getesteten metrics-Funktionen.

Alle DB-Schreib-Tests nutzen einen temp-DB-Pfad (db_path-Param) — es wird NIE die
echte training.db angefasst.
"""

import json

import pytest
from starlette.testclient import TestClient

from src.data.store import import_file, import_files, recent_workouts
from src.web.app import app

client = TestClient(app)


# Synthetischer GPX-Track: 3 Punkte, 60 s auseinander (→ Dauer 2 min), mit HF.
# Koordinaten ~111 m / 0.001° Breite → messbare Distanz.
SYNTHETIC_GPX = """<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="sirdar-test"
     xmlns="http://www.topografix.com/GPX/1/1"
     xmlns:gpxtpx="http://www.garmin.com/xmlschemas/TrackPointExtension/v1">
  <trk>
    <name>Test Ride</name>
    <type>cycling</type>
    <trkseg>
      <trkpt lat="48.1000" lon="11.5000">
        <time>2026-06-01T08:00:00Z</time>
        <extensions><gpxtpx:TrackPointExtension><gpxtpx:hr>120</gpxtpx:hr></gpxtpx:TrackPointExtension></extensions>
      </trkpt>
      <trkpt lat="48.1010" lon="11.5000">
        <time>2026-06-01T08:01:00Z</time>
        <extensions><gpxtpx:TrackPointExtension><gpxtpx:hr>140</gpxtpx:hr></gpxtpx:TrackPointExtension></extensions>
      </trkpt>
      <trkpt lat="48.1020" lon="11.5000">
        <time>2026-06-01T08:02:00Z</time>
        <extensions><gpxtpx:TrackPointExtension><gpxtpx:hr>160</gpxtpx:hr></gpxtpx:TrackPointExtension></extensions>
      </trkpt>
    </trkseg>
  </trk>
</gpx>
"""


@pytest.fixture()
def gpx_file(tmp_path):
    p = tmp_path / "test_ride.gpx"
    p.write_text(SYNTHETIC_GPX, encoding="utf-8")
    return p


@pytest.fixture()
def temp_db(tmp_path):
    return tmp_path / "test_training.db"


# ─── Parsing + Persistenz ────────────────────────────────────

def test_import_gpx_writes_workout(gpx_file, temp_db):
    """import_file parst die GPX und schreibt ein Workout in die temp-DB."""
    result = import_file(gpx_file, db_path=temp_db)
    assert result["status"] == "imported"

    rows = recent_workouts(db_path=temp_db)
    assert len(rows) == 1
    w = rows[0]
    assert w["datum"] == "2026-06-01"
    assert w["sportart"] == "cycling"
    assert w["dauer"] == pytest.approx(2.0, abs=0.01)       # 2 Minuten
    assert w["distanz"] is not None and w["distanz"] > 0    # messbare Distanz
    assert w["avg_hr"] == 140                               # Mittel(120,140,160)
    assert w["np"] is None                                  # GPX ohne Power
    assert w["if_"] is None
    assert w["tss"] is None
    assert w["quelle"] == "file_import"
    assert w["raw_ref"] == "test_ride.gpx"


def test_import_gpx_dedup(gpx_file, temp_db):
    """Erneuter Import derselben Datei wird übersprungen (kein Duplikat)."""
    first = import_file(gpx_file, db_path=temp_db)
    second = import_file(gpx_file, db_path=temp_db)
    assert first["status"] == "imported"
    assert second["status"] == "skipped"
    assert len(recent_workouts(db_path=temp_db)) == 1


def test_import_files_summary(gpx_file, temp_db):
    """import_files liefert eine korrekte Zusammenfassung (1 importiert, 1 übersprungen)."""
    summary = import_files([gpx_file, gpx_file], db_path=temp_db)
    assert summary["imported"] == 1
    assert summary["skipped"] == 1
    assert summary["errors"] == 0


def test_import_unsupported_suffix(tmp_path, temp_db):
    """Unbekannte Endung → status 'error', nichts in der DB."""
    bad = tmp_path / "note.txt"
    bad.write_text("nope", encoding="utf-8")
    result = import_file(bad, db_path=temp_db)
    assert result["status"] == "error"
    assert recent_workouts(db_path=temp_db) == []


# ─── Web-Upload-Seite ────────────────────────────────────────

def test_import_page_renders():
    """GET /import → 200 und enthält das Upload-Formular."""
    r = client.get("/import")
    assert r.status_code == 200
    assert 'name="files"' in r.text
    assert 'enctype="multipart/form-data"' in r.text


def test_import_post_upload_imports_gpx():
    """POST /import mit einer GPX-Datei importiert sie und zeigt die Zusammenfassung.

    Nutzt die session-weite DB-Umleitung aus conftest (keine echte training.db).
    Regressions-Schutz: request.form() liefert starlette.UploadFile, nicht
    fastapi.UploadFile — der Route-Filter muss gegen die richtige Klasse prüfen.
    """
    from src.data.store import recent_workouts as _recent

    before = len(_recent())
    r = client.post(
        "/import",
        files=[("files", ("posted_ride.gpx", SYNTHETIC_GPX.encode("utf-8"), "application/gpx+xml"))],
    )
    assert r.status_code == 200
    # Genau ein neues Workout in der (umgeleiteten) DB.
    rows = _recent(limit=50)
    assert len(rows) == before + 1
    assert any(w["raw_ref"] == "posted_ride.gpx" for w in rows)
