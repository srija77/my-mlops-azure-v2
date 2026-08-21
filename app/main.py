"""
main.py
=======
FastAPI application in front of the Azure ML managed online endpoint.

The endpoint speaks a 37-column tabular contract over a keyed HTTPS route. That
is correct for machine-to-machine use and unusable for a human, so this app adds
the layer the architecture was missing:

  GET  /               operator UI — grouped form, sample loader, result panel
  POST /predict        form submission, re-renders the page with the prediction
  POST /api/predict    JSON API for machine callers
  GET  /api/sample     the canonical sample row
  GET  /health         liveness — process is up. No outbound calls.
  GET  /ready          readiness — configuration is present.

Health and readiness are deliberately separate. Liveness must never depend on the
scoring endpoint: if it did, an endpoint outage would make the platform kill and
restart healthy app replicas, turning a partial outage into a total one.
"""

from __future__ import annotations

import logging
import os
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .aml_client import AmlClient, ScoringError
from .config import load_settings
from .schema import BINARY_FEATURES, FEATURE_GROUPS, FEATURE_ORDER, SAMPLE_ROW

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
log = logging.getLogger("app")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

settings = load_settings()
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))
client = AmlClient(settings)

@asynccontextmanager
async def lifespan(_: FastAPI):
    log.info("starting revision=%s config=%s", settings.revision, settings.redacted())
    if not settings.configured:
        log.warning("scoring endpoint not configured; /ready will report not-ready")
    yield


app = FastAPI(
    title="DAM MCP Forecasting",
    description="Operator UI and JSON API in front of the Azure ML scoring endpoint.",
    version=settings.revision,
    lifespan=lifespan,
)
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")


def _coerce(name: str, raw: str) -> float:
    """Parse one submitted field, with a message that names the offending field."""
    text = (raw or "").strip()
    if text == "":
        raise ScoringError(f"'{name}' is required.")
    try:
        value = float(text)
    except ValueError as exc:
        raise ScoringError(f"'{name}' must be a number (received {text!r}).") from exc
    if name in BINARY_FEATURES and value not in (0.0, 1.0):
        raise ScoringError(f"'{name}' is a flag and must be 0 or 1 (received {value}).")
    return value


def _page(request: Request, *, values: dict[str, Any], result: dict[str, Any] | None = None,
          error: str | None = None, status: int = 200) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "groups": FEATURE_GROUPS,
            "binary": BINARY_FEATURES,
            "values": values,
            "result": result,
            "error": error,
            "configured": settings.configured,
            "revision": settings.revision,
            "model_name": settings.model_name,
        },
        status_code=status,
    )


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    return _page(request, values=dict(SAMPLE_ROW))


@app.post("/predict", response_class=HTMLResponse)
async def predict_form(request: Request) -> HTMLResponse:
    form = await request.form()
    submitted = {name: str(form.get(name, "")) for name in FEATURE_ORDER}
    try:
        features = {name: _coerce(name, submitted[name]) for name in FEATURE_ORDER}
    except ScoringError as exc:
        return _page(request, values=submitted, error=str(exc), status=400)

    started = time.perf_counter()
    try:
        body = await client.predict(features)
    except ScoringError as exc:
        log.warning("scoring failed: %s", exc)
        return _page(request, values=submitted, error=str(exc), status=502)

    elapsed_ms = (time.perf_counter() - started) * 1000
    result = {
        "prediction": body["predictions"][0],
        "model": body.get("model", settings.model_name),
        "elapsed_ms": round(elapsed_ms, 1),
    }
    log.info("scored in %.1fms -> %s", elapsed_ms, result["prediction"])
    return _page(request, values=submitted, result=result)


@app.post("/api/predict")
async def predict_api(payload: dict[str, Any]) -> JSONResponse:
    features = payload.get("features", payload)
    if not isinstance(features, dict):
        return JSONResponse({"error": "Body must be a feature object, or {\"features\": {...}}."},
                            status_code=400)
    try:
        parsed = {name: _coerce(name, str(features.get(name, ""))) for name in FEATURE_ORDER}
        body = await client.predict(parsed)
    except ScoringError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400 if exc.status is None else 502)
    return JSONResponse({
        "prediction": body["predictions"][0],
        "model": body.get("model", settings.model_name),
        "revision": settings.revision,
    })


@app.get("/api/sample")
async def sample() -> dict[str, Any]:
    return {"features": SAMPLE_ROW}


@app.get("/health")
async def health() -> dict[str, str]:
    """Liveness. Must not call the scoring endpoint — see the module docstring."""
    return {"status": "ok", "revision": settings.revision}


@app.get("/ready")
async def ready() -> JSONResponse:
    if not settings.configured:
        return JSONResponse(
            {"status": "not-ready", "reason": "AML_ENDPOINT_URL or AML_ENDPOINT_KEY is unset"},
            status_code=503,
        )
    return JSONResponse({"status": "ready", "revision": settings.revision})
