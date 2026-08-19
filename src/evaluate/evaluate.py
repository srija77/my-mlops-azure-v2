"""
evaluate.py
===========
Azure ML pipeline component: STAGE 3 (evaluate).

Loads the latest version of the registered model from the Azure ML Model
Registry, scores it on the holdout split (same time-based split as train.py),
logs eval metrics + plots to the model's MLflow run, tags the model version
with pass/fail, and writes eval_metrics.json. Exits non-zero if the model fails
the quality gate — which stops the pipeline before promotion.

Mirrors src/5_evaluation/1_evaluate.py from the source project, retargeted at
the AML workspace registry.
"""

# ruff: noqa: I001  (matplotlib.use("Agg") must sit between imports; keep order)
from __future__ import annotations

import argparse
import json
import logging
import math
import sys
import tempfile
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import mlflow  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

TARGET_COL = "dam_mcp"
TIMESTAMP_COL = "event_timestamp"
FEATURES_FILENAME = "march_2025_features.parquet"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [EVALUATE] %(message)s")
log = logging.getLogger("evaluate")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Evaluate best model and gate promotion")
    p.add_argument("--features-dir", required=True)
    p.add_argument("--metrics-dir", required=True, help="Folder to write eval_metrics.json")
    # Ordering-only edge: forces this step to run after train (which registers
    # the model version this script loads). The path itself is not read.
    p.add_argument("--train-signal", default=None, help=argparse.SUPPRESS)
    p.add_argument("--registered-model-name", default="dam_mcp_forecast")
    p.add_argument("--experiment", default="dam_mcp_forecast")
    p.add_argument("--test-size", type=float, default=0.2)
    p.add_argument("--rmse-threshold", type=float, default=None)
    p.add_argument("--mape-threshold", type=float, default=None)
    return p.parse_args()


def load_latest_model(model_name: str):
    client = mlflow.MlflowClient()
    versions = client.search_model_versions(f"name='{model_name}'")
    if not versions:
        raise RuntimeError(f"No versions for '{model_name}'. Run train first.")
    latest = max(versions, key=lambda v: int(v.version))
    model = mlflow.sklearn.load_model(f"models:/{model_name}/{latest.version}")
    run = client.get_run(latest.run_id)
    log.info(f"Loaded {model_name} v{latest.version} (run={run.info.run_name})")
    return model, latest.run_id, run.info.run_name, latest.version, client


def fetch_and_split(features_dir: str, test_size: float):
    path = Path(features_dir)
    fp = path / FEATURES_FILENAME if path.is_dir() else path
    if not fp.exists():
        fp = sorted(path.glob("*.parquet"))[0]
    df = pd.read_parquet(fp)
    df[TIMESTAMP_COL] = pd.to_datetime(df[TIMESTAMP_COL])
    df = df.sort_values(TIMESTAMP_COL).reset_index(drop=True)

    y = df[TARGET_COL]
    exclude = {TARGET_COL, TIMESTAMP_COL, "block_id"}
    X = df[[c for c in df.columns if c not in exclude]]
    for col in X.select_dtypes(exclude=["object", "string"]).columns:
        if pd.api.types.is_integer_dtype(X[col]):
            X[col] = X[col].astype(float)

    n = max(1, int(len(X) * (1 - test_size)))
    log.info(f"Eval set: {len(X) - n} rows (last {test_size*100:.0f}% by time)")
    return X.iloc[n:], y.iloc[n:], df[TIMESTAMP_COL].iloc[n:]


def compute_metrics(y_true, y_pred) -> dict:
    return {
        "eval_rmse": math.sqrt(mean_squared_error(y_true, y_pred)),
        "eval_mae": mean_absolute_error(y_true, y_pred),
        "eval_mape": float(np.mean(np.abs((y_true - y_pred) / (y_true + 1e-8))) * 100),
        "eval_r2": r2_score(y_true, y_pred),
    }


def log_eval_plots(y_true, y_pred, ts):
    with tempfile.TemporaryDirectory() as tmp:
        fig, ax = plt.subplots(figsize=(12, 4))
        ax.plot(ts.values, y_true, label="Actual", linewidth=0.8)
        ax.plot(ts.values, y_pred, label="Predicted", linewidth=0.8)
        ax.set_title("Evaluation: Predictions vs Actuals")
        ax.legend()
        fig.tight_layout()
        p = Path(tmp) / "eval_pred_vs_actual.png"
        fig.savefig(p, dpi=100)
        plt.close(fig)
        mlflow.log_artifact(str(p), artifact_path="eval_plots")

        fig, ax = plt.subplots(figsize=(5, 5))
        ax.scatter(y_true, y_pred, alpha=0.3, s=5)
        mn, mx = min(y_true.min(), y_pred.min()), max(y_true.max(), y_pred.max())
        ax.plot([mn, mx], [mn, mx], "r--", linewidth=0.8)
        ax.set_xlabel("Actual")
        ax.set_ylabel("Predicted")
        ax.set_title("Evaluation: Actual vs Predicted")
        fig.tight_layout()
        p = Path(tmp) / "eval_scatter.png"
        fig.savefig(p, dpi=100)
        plt.close(fig)
        mlflow.log_artifact(str(p), artifact_path="eval_plots")


def check_gate(m: dict, rmse_thr, mape_thr) -> bool:
    passed = True
    if rmse_thr is not None and m["eval_rmse"] > rmse_thr:
        log.warning(f"FAILED: RMSE {m['eval_rmse']:.2f} > {rmse_thr}")
        passed = False
    if mape_thr is not None and m["eval_mape"] > mape_thr:
        log.warning(f"FAILED: MAPE {m['eval_mape']:.2f}% > {mape_thr}%")
        passed = False
    if passed:
        log.info(f"PASSED gate: RMSE={m['eval_rmse']:.2f} MAPE={m['eval_mape']:.2f}%")
    return passed


def main() -> None:
    args = parse_args()
    from src.common.config import get_mlflow_tracking_uri
    mlflow.set_tracking_uri(get_mlflow_tracking_uri())
    mlflow.set_experiment(args.experiment)

    model, run_id, run_name, version, client = load_latest_model(args.registered_model_name)
    X_test, y_test, ts_test = fetch_and_split(args.features_dir, args.test_size)

    y_pred = model.predict(X_test)
    metrics = compute_metrics(y_test.values, y_pred)
    passed = check_gate(metrics, args.rmse_threshold, args.mape_threshold)
    status = "PASSED" if passed else "FAILED"

    with mlflow.start_run(run_id=run_id):
        mlflow.log_metrics(metrics)
        mlflow.set_tag("passed_eval", str(passed))
        mlflow.set_tag("eval_status", status)
        log_eval_plots(y_test, y_pred, ts_test)

    # Tag the registry model version so promotion can gate on it.
    for k, v in {"passed_eval": str(passed), "eval_status": status,
                 "eval_rmse": f"{metrics['eval_rmse']:.4f}",
                 "eval_mape": f"{metrics['eval_mape']:.2f}"}.items():
        client.set_model_version_tag(args.registered_model_name, str(version), k, v)

    Path(args.metrics_dir).mkdir(parents=True, exist_ok=True)
    with open(Path(args.metrics_dir) / "eval_metrics.json", "w") as f:
        json.dump({"evaluated_model": run_name, "evaluated_run_id": run_id,
                   "model_version": str(version), "passed": passed, **metrics}, f, indent=2)

    log.info("=" * 55)
    log.info(f"  Model v{version}: RMSE={metrics['eval_rmse']:.2f} "
             f"MAPE={metrics['eval_mape']:.2f}% R2={metrics['eval_r2']:.4f} -> {status}")
    log.info("=" * 55)

    if not passed:
        sys.exit(1)


if __name__ == "__main__":
    main()
