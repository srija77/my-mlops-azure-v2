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

DATA COLLECTION (architecture diagram edge: EP -. inference data .-> MON)
------------------------------------------------------------------------
Enabling `data_collector` in aml/endpoints/deployment.yml opens the channel, but
Azure only captures what this script explicitly hands it. The two Collectors
below write each request's features to the `model_inputs` collection and each
prediction to `model_outputs`, which is exactly what aml/monitoring.yml reads as
production data for data drift, prediction drift, and data quality.

The prediction frame is deliberately named `dam_mcp` — the same column as the
training target in the reference baseline — so prediction_drift compares like
with like instead of finding no overlapping column.

Collection is best-effort: if the package is missing or the deployment has
collection disabled, scoring still succeeds. A monitoring failure must never
take down inference.
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

# Column name the reference baseline uses for the target; keeping the collected
# prediction under the same name is what makes prediction_drift comparable.
_PREDICTION_COL = "dam_mcp"

_inputs_collector = None
_outputs_collector = None


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


def _init_collectors() -> None:
    """Wire up production data collection. Never fatal."""
    global _inputs_collector, _outputs_collector
    try:
        from azureml.ai.monitoring import Collector

        _inputs_collector = Collector(
            name="model_inputs",
            on_error=lambda e: log.warning(f"model_inputs collection failed: {e}"),
        )
        _outputs_collector = Collector(
            name="model_outputs",
            on_error=lambda e: log.warning(f"model_outputs collection failed: {e}"),
        )
        log.info("Data collection enabled (model_inputs + model_outputs)")
    except Exception as exc:  # noqa: BLE001
        # Package absent locally, or data_collector disabled on the deployment.
        log.warning(f"Data collection unavailable ({exc}); serving without it.")
        _inputs_collector = None
        _outputs_collector = None


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
            _init_collectors()
            return
    except Exception as exc:  # noqa: BLE001
        log.warning(f"MLflow load failed ({exc}); trying pickle.")

    with open(_find_model_file(model_root), "rb") as f:
        _model = pickle.load(f)
    log.info("Loaded model via pickle")
    _init_collectors()


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

        # EP -. inference data .-> MON : capture the request features. `context`
        # carries the correlation id that joins this row to its prediction.
        context = None
        if _inputs_collector is not None:
            try:
                context = _inputs_collector.collect(df)
            except Exception as exc:  # noqa: BLE001
                log.warning(f"Input collection skipped: {exc}")

        preds = _model.predict(df)
        preds = np.asarray(preds).ravel().tolist()

        if _outputs_collector is not None:
            try:
                out_df = pd.DataFrame({_PREDICTION_COL: preds})
                if context is not None:
                    _outputs_collector.collect(out_df, context)
                else:
                    _outputs_collector.collect(out_df)
            except Exception as exc:  # noqa: BLE001
                log.warning(f"Output collection skipped: {exc}")

        return {"predictions": preds, "model": _model_name, "n": len(preds)}
    except Exception as exc:  # noqa: BLE001
        log.exception("Scoring failed")
        return {"error": str(exc)}
