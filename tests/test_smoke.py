"""Phase-0 Smoke-Tests: App startet, Routen antworten, DB-Schema vollständig, i18n greift.

Laufen ohne echten Server (Starlette TestClient) und ohne Claude-CLI/Token.
"""

import tempfile
from pathlib import Path

from starlette.testclient import TestClient

from src.core import db
from src.web.app import app

client = TestClient(app)


def test_db_schema_complete():
    tmp = Path(tempfile.mkdtemp()) / "t.db"
    db.init_db(tmp)
    assert set(db.EXPECTED_TABLES) <= set(db.list_tables(tmp))


def test_dashboard_renders():
    r = client.get("/")
    assert r.status_code == 200
    assert "Sirdar" in r.text


def test_api_status():
    r = client.get("/api/status")
    assert r.status_code == 200
    assert r.json()["app"] == "Sirdar"


def test_pwa_assets():
    assert client.get("/manifest.webmanifest").status_code == 200
    assert client.get("/sw.js").status_code == 200


def test_language_switch():
    r = client.get("/set-lang/en", follow_redirects=False)
    assert r.status_code == 303
    assert "sirdar_lang=en" in r.headers.get("set-cookie", "")
    de = client.get("/", headers={"cookie": "sirdar_lang=de"}).text
    en = client.get("/", headers={"cookie": "sirdar_lang=en"}).text
    assert de != en, "DE und EN rendern identisch — i18n greift nicht"
