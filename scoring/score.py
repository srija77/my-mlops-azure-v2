"""
score.py
========
Scoring entry point for the Azure ML Managed Online Endpoint. This is the Azure
equivalent of the source project's Flask `/predict` route — but Azure ML runs
the web server, health checks, autoscaling, and logging for you.

Contract:
  init() — runs once when the container starts. Loads the champion model that
           AML mounts at AZUREML_MODEL_DIR.
  run(raw_data) — runs per request. Accepts JSON with either:
      {"data": [ {feature: value, ...}, ... ]}   # list of feature rows
      {"input_data": {"columns": [...], "data": [[...]]}}  # AML tabular format
    and returns {"predictions": [...], "model": "...", "n": N}.

A ready-to-use request body is in scoring/sample_request.json (one real March
2025 feature row). The model's preprocessor expects the full feature column set,
so an empty body returns an error rather than a guessed prediction.
"""

from __future__ import annotations

import json
import logging
import os
import pickle
from pathlib import Path

import numpy as np
import pandas as pd

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("score")

_model = None
_model_name = "dam_mcp_forecast"


def _find_model_file(model_dir: Path) -> Path:
    """Locate the serialized model regardless of how it was registered.

    - MLflow-registered sklearn models unpack to <dir>/model/model.pkl
    - A raw pickle registered as an artifact lands as <dir>/model.pkl
    """
    for candidate in [model_dir / "model" / "model.pkl", model_dir / "model.pkl"]:
        if candidate.exists():
            return candidate
    pkls = list(model_dir.rglob("*.pkl"))
    if pkls:
        return pkls[0]
    raise FileNotFoundError(f"No model .pkl found under {model_dir}")


def init() -> None:
    global _model
    model_root = Path(os.environ.get("AZUREML_MODEL_DIR", "."))
    log.info(f"AZUREML_MODEL_DIR = {model_root}")

    # Prefer MLflow loader (handles the sklearn flavor + signature); fall back
    # to a plain pickle load for models saved as a raw .pkl artifact.
    try:
        import mlflow.sklearn
        mlflow_dir = model_root / "model"
        if (mlflow_dir / "MLmodel").exists():
            _model = mlflow.sklearn.load_model(str(mlflow_dir))
            log.info("Loaded model via mlflow.sklearn")
            return
    except Exception as exc:  # noqa: BLE001
        log.warning(f"MLflow load failed ({exc}); trying pickle.")

    with open(_find_model_file(model_root), "rb") as f:
        _model = pickle.load(f)
    log.info("Loaded model via pickle")


def _to_frame(payload: dict) -> pd.DataFrame:
    if "input_data" in payload:  # AML tabular contract
        blob = payload["input_data"]
        return pd.DataFrame(blob["data"], columns=blob["columns"])
    if "data" in payload:        # list of feature dicts
        return pd.DataFrame(payload["data"])
    raise ValueError(
        "Request must contain 'input_data' (columns+data) or 'data' (list of "
        "feature dicts). See scoring/sample_request.json."
    )


def run(raw_data: str):
    try:
        payload = json.loads(raw_data) if isinstance(raw_data, str) else raw_data
        df = _to_frame(payload)
        preds = _model.predict(df)
        preds = np.asarray(preds).ravel().tolist()
        return {"predictions": preds, "model": _model_name, "n": len(preds)}
    except Exception as exc:  # noqa: BLE001
        log.exception("Scoring failed")
        return {"error": str(exc)}
