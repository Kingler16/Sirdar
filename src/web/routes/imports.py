"""Datei-Import-Routen für Sirdar (Phase 1B).

Seiten:
  GET  /import   -> Upload-Formular (.fit/.gpx, multiple) + Tabelle der letzten Workouts
  POST /import   -> nimmt hochgeladene Dateien, parst+importiert, zeigt Zusammenfassung

Hochgeladene Dateien werden in ein temporäres Verzeichnis geschrieben, geparst und
in die ``workouts``-Tabelle geschrieben (``src/data/store``). Die temporären Dateien
werden danach wieder gelöscht — die DB ist die Quelle der Wahrheit.
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
# request.form() liefert starlette.datastructures.UploadFile — NICHT fastapi.UploadFile
# (die beiden sind nicht identisch; isinstance gegen die starlette-Klasse prüfen).
from starlette.datastructures import UploadFile

from src.data.store import SUPPORTED_SUFFIXES, import_files, recent_workouts
from src.web.deps import ctx, templates

logger = logging.getLogger(__name__)

router = APIRouter()

# Maximale Anzahl Workouts in der Übersichtstabelle.
_RECENT_LIMIT = 10


@router.get("/import", response_class=HTMLResponse)
async def import_page(request: Request):
    """Zeigt das Upload-Formular und die letzten Workouts."""
    workouts = recent_workouts(limit=_RECENT_LIMIT)
    return templates.TemplateResponse(
        request, "import.html",
        ctx(request, "import", workouts=workouts, summary=None),
    )


@router.post("/import", response_class=HTMLResponse)
async def import_upload(request: Request):
    """Nimmt hochgeladene FIT/GPX-Dateien, importiert sie und zeigt das Ergebnis."""
    form = await request.form()
    uploads: list[UploadFile] = [
        f for f in form.getlist("files") if isinstance(f, UploadFile) and f.filename
    ]

    summary = {"imported": 0, "skipped": 0, "errors": 0, "results": []}

    if uploads:
        # Dateien temporär ablegen (Originalnamen für raw_ref/Dedup behalten),
        # importieren, danach das Temp-Verzeichnis aufräumen.
        with tempfile.TemporaryDirectory(prefix="sirdar_import_") as tmpdir:
            tmp_paths: list[Path] = []
            for upload in uploads:
                suffix = Path(upload.filename).suffix.lower()
                if suffix not in SUPPORTED_SUFFIXES:
                    summary["errors"] += 1
                    summary["results"].append(
                        {"status": "error", "raw_ref": upload.filename,
                         "error": f"Nicht unterstützt: {suffix}"}
                    )
                    continue
                # Originalnamen erhalten → korrekte raw_ref & Dedup.
                dest = Path(tmpdir) / Path(upload.filename).name
                dest.write_bytes(await upload.read())
                tmp_paths.append(dest)

            if tmp_paths:
                result = import_files(tmp_paths)
                summary["imported"] += result["imported"]
                summary["skipped"] += result["skipped"]
                summary["errors"] += result["errors"]
                summary["results"].extend(result["results"])

    workouts = recent_workouts(limit=_RECENT_LIMIT)
    return templates.TemplateResponse(
        request, "import.html",
        ctx(request, "import", workouts=workouts, summary=summary),
    )
