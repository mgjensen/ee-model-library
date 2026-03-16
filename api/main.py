"""
api/main.py

FastAPI application for the EE Model Library.

Endpoints:
    POST /run        — accept ProjectConfig JSON, return assembled .xlsx workbook
    GET  /modules    — list registered modules from module_registry.json
    GET  /health     — liveness check
"""

from __future__ import annotations

import json
import os
import tempfile

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from starlette.background import BackgroundTask

from assembly.engine import ProjectConfig, run
from assembly.excel_writer import write_workbook

app = FastAPI(
    title="EE Model Library API",
    description=(
        "Backend service for assembling production-ready Excel financial models "
        "for renewable energy projects. Accepts a ProjectConfig payload and returns "
        "a fully populated .xlsx workbook."
    ),
    version="1.0.0",
)

_REGISTRY_PATH = os.path.join(
    os.path.dirname(__file__), "..", "registry", "module_registry.json"
)


# ============================================================================
# ENDPOINTS
# ============================================================================

@app.get("/health")
def health() -> dict:
    """Liveness check."""
    return {"status": "ok", "version": app.version}


@app.get("/modules")
def list_modules() -> JSONResponse:
    """
    Return the full module registry.

    Response is the contents of registry/module_registry.json keyed by module_id.
    """
    try:
        with open(_REGISTRY_PATH, "r", encoding="utf-8") as f:
            registry = json.load(f)
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail="Module registry not found.")
    return JSONResponse(content=registry)


@app.post("/run")
def run_model(config: ProjectConfig) -> FileResponse:
    """
    Run all enabled modules in dependency order and return a .xlsx workbook.

    Request body:  ProjectConfig (JSON)
    Response:      application/vnd.openxmlformats-officedocument.spreadsheetml.sheet

    Raises 422 if the config fails Pydantic validation.
    Raises 500 if the model run fails unexpectedly.
    """
    try:
        result = run(config)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Model run failed: {exc}")

    tmp = tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False)
    tmp.close()
    try:
        write_workbook(result, config, tmp.name)
    except Exception as exc:
        os.unlink(tmp.name)
        raise HTTPException(status_code=500, detail=f"Excel write failed: {exc}")

    safe_name = config.project_name.replace(" ", "_").replace("/", "-")
    filename = f"{safe_name}.xlsx"

    return FileResponse(
        path=tmp.name,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=filename,
        background=BackgroundTask(os.unlink, tmp.name),
    )
