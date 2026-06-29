"""Phase-1C Tests: Dashboard (/) + Morgen-Check (/health/log).

Nutzt die session-weite DB-Umleitung aus conftest (keine echte training.db).
Prüft: Dashboard rendert mit UND ohne Daten (graceful), Health-Formular rendert,
POST persistiert in health_metrics (upsert pro Datum).
"""

from starlette.testclient import TestClient

from src.data.store import get_health_metrics, upsert_health_metrics
from src.web.app import app

client = TestClient(app)


# ─── Dashboard (/) ───────────────────────────────────────────

def test_dashboard_renders_without_data():
    """GET / → 200, auch ohne Health-/Last-Daten (graceful Empty-State)."""
    r = client.get("/")
    assert r.status_code == 200
    # Readiness-Karte ist da; ohne Daten zeigt sie den CTA zum Morgen-Check.
    assert "/health/log" in r.text


def test_dashboard_renders_with_health_data():
    """Mit Health-Daten in der (umgeleiteten) DB rendert / weiterhin 200."""
    upsert_health_metrics("2024-04-01", {"hrv": 50, "rhr": 50, "schlaf_h": 8})
    upsert_health_metrics("2024-04-02", {"hrv": 51, "rhr": 49, "schlaf_h": 7.5})
    upsert_health_metrics("2024-04-03", {"hrv": 52, "rhr": 48, "schlaf_h": 8})
    upsert_health_metrics("2024-04-04", {"hrv": 49, "rhr": 51, "schlaf_h": 7})
    r = client.get("/")
    assert r.status_code == 200
    # Chart-Lib wird eingebunden (lokal gevendort, kein CDN).
    assert "/static/js/chart.umd.min.js" in r.text


def test_dashboard_loads_chartjs_locally():
    """Dashboard bindet Chart.js lokal ein (kein externes CDN)."""
    r = client.get("/")
    assert "/static/js/chart.umd.min.js" in r.text
    assert "cdn.jsdelivr.net" not in r.text
    assert "cdnjs" not in r.text


def test_chartjs_asset_served():
    """Die gevendorte Chart.js-Datei ist unter /static erreichbar."""
    r = client.get("/static/js/chart.umd.min.js")
    assert r.status_code == 200
    assert "Chart" in r.text


# ─── Morgen-Check (/health/log) ──────────────────────────────

def test_health_log_page_renders():
    """GET /health/log → 200 mit dem Eingabeformular."""
    r = client.get("/health/log")
    assert r.status_code == 200
    assert 'name="hrv"' in r.text
    assert 'name="rhr"' in r.text
    assert 'name="schlaf_h"' in r.text
    assert 'action="/health/log"' in r.text


def test_health_log_post_persists():
    """POST /health/log schreibt die Werte in health_metrics (temp-DB)."""
    r = client.post(
        "/health/log",
        data={
            "datum": "2024-05-01",
            "hrv": "55.5",
            "rhr": "48",
            "schlaf_h": "7.8",
            "schlaf_qualitaet": "4",
            "soreness": "2",
            "stimmung": "5",
            "gewicht": "72.3",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303  # PRG: Redirect nach Speichern

    saved = get_health_metrics("2024-05-01")
    assert saved is not None
    assert saved["hrv"] == 55.5
    assert saved["rhr"] == 48
    assert saved["schlaf_h"] == 7.8
    assert saved["stimmung"] == 5
    assert saved["gewicht"] == 72.3
    assert saved["quelle"] == "manual"


def test_health_log_post_upsert_same_date():
    """Zweiter POST fürs gleiche Datum aktualisiert (kein Duplikat)."""
    client.post("/health/log", data={"datum": "2024-05-02", "rhr": "50"},
                follow_redirects=False)
    client.post("/health/log", data={"datum": "2024-05-02", "rhr": "44"},
                follow_redirects=False)
    saved = get_health_metrics("2024-05-02")
    assert saved["rhr"] == 44  # überschrieben, nicht dupliziert


def test_health_log_empty_fields_stay_none():
    """Leere Felder werden nicht als 0 gespeichert, sondern bleiben None."""
    client.post("/health/log", data={"datum": "2024-05-03", "hrv": "60"},
                follow_redirects=False)
    saved = get_health_metrics("2024-05-03")
    assert saved["hrv"] == 60
    assert saved["rhr"] is None
    assert saved["gewicht"] is None
