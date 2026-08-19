"""
train.py
========
Azure ML pipeline component: STAGE 2 (train).

Trains 5 forecasting models to predict DAM MCP, logs each to MLflow (which,
inside an Azure ML job, points at the workspace tracking server automatically),
saves the best sklearn pipeline + metrics to the output folder, and registers
the best model into the Azure ML Model Registry.

Models: ARIMA, Exponential Smoothing (Holt-Winters), Prophet (optional),
        Gradient Boosting, XGBoost. Target: dam_mcp.

This mirrors the source project's src/4_training/1_train.py, refactored to read
the features parquet from --features-dir (the previous component's output) and
write to --model-dir / --metrics-dir, and to resolve MLflow from the AML
workspace instead of a local http://127.0.0.1:5000 server.

Usage (local test, after `az login`):
    python src/train/train.py \
        --features-dir data/features \
        --model-dir outputs/model --metrics-dir outputs
"""

# ruff: noqa: I001  (matplotlib.use("Agg") must sit between imports; keep order)
from __future__ import annotations

import argparse
import json
import logging
import math
import pickle
import sys
import tempfile
import warnings
from pathlib import Path
from typing import Dict, List

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import mlflow  # noqa: E402
import mlflow.sklearn  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from mlflow.models import infer_signature  # noqa: E402
from sklearn.compose import ColumnTransformer  # noqa: E402
from sklearn.ensemble import GradientBoostingRegressor  # noqa: E402
from sklearn.impute import SimpleImputer  # noqa: E402
from sklearn.metrics import mean_absolute_error, mean_squared_error  # noqa: E402
from sklearn.pipeline import Pipeline  # noqa: E402
from sklearn.preprocessing import OneHotEncoder, StandardScaler  # noqa: E402
from statsmodels.tsa.arima.model import ARIMA  # noqa: E402
from statsmodels.tsa.holtwinters import ExponentialSmoothing  # noqa: E402
from xgboost import XGBRegressor  # noqa: E402

warnings.filterwarnings("ignore")

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

TARGET_COL = "dam_mcp"
TIMESTAMP_COL = "event_timestamp"
FEATURES_FILENAME = "march_2025_features.parquet"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [TRAIN] %(message)s")
log = logging.getLogger("train")


# -- I/O ---------------------------------------------------------------------
def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Train 5 forecasting models on AML")
    p.add_argument("--features-dir", required=True, help="Folder with march_2025_features.parquet")
    p.add_argument("--model-dir", required=True, help="Folder to write model.pkl")
    p.add_argument("--metrics-dir", required=True, help="Folder to write metrics.json")
    p.add_argument("--test-size", type=float, default=0.2)
    p.add_argument("--experiment", default="dam_mcp_forecast")
    p.add_argument("--registered-model-name", default="dam_mcp_forecast")
    return p.parse_args()


def load_features(features_dir: str) -> pd.DataFrame:
    path = Path(features_dir)
    fp = path / FEATURES_FILENAME if path.is_dir() else path
    if not fp.exists():
        parquets = sorted(path.glob("*.parquet"))
        if not parquets:
            raise FileNotFoundError(f"No features parquet in {features_dir}")
        fp = parquets[0]
    df = pd.read_parquet(fp)
    log.info(f"Loaded features: {df.shape[0]} rows, {df.shape[1]} cols from {fp}")
    return df


# -- Prep --------------------------------------------------------------------
def prepare_data(df: pd.DataFrame, test_size: float):
    df = df.copy()
    df[TIMESTAMP_COL] = pd.to_datetime(df[TIMESTAMP_COL])
    df = df.sort_values(TIMESTAMP_COL).reset_index(drop=True)

    timestamps = df[[TIMESTAMP_COL]]
    y = df[TARGET_COL]

    exclude_cols = {TARGET_COL, TIMESTAMP_COL, "block_id"}
    feature_cols = [c for c in df.columns if c not in exclude_cols]
    X = df[feature_cols]

    cat_cols = X.select_dtypes(include=["object", "string"]).columns.tolist()
    num_cols = [c for c in X.columns if c not in cat_cols]
    for col in num_cols:
        if pd.api.types.is_integer_dtype(X[col]):
            X[col] = X[col].astype(float)

    n = max(1, int(len(X) * (1 - test_size)))
    log.info(f"Train size: {n}, Test size: {len(X) - n} | "
             f"num={len(num_cols)} cat={len(cat_cols)}")
    return (X.iloc[:n], X.iloc[n:], y.iloc[:n], y.iloc[n:],
            timestamps.iloc[:n], timestamps.iloc[n:], cat_cols, num_cols)


def build_preprocessor(cat_cols: List[str], num_cols: List[str]) -> ColumnTransformer:
    num_pipe = Pipeline([("imputer", SimpleImputer(strategy="median")),
                         ("scaler", StandardScaler())])
    cat_pipe = Pipeline([("imputer", SimpleImputer(strategy="most_frequent")),
                         ("encoder", OneHotEncoder(handle_unknown="ignore"))])
    return ColumnTransformer(
        transformers=[("num", num_pipe, num_cols), ("cat", cat_pipe, cat_cols)],
        remainder="drop",
    )


def compute_metrics(y_true, y_pred) -> Dict[str, float]:
    rmse = math.sqrt(mean_squared_error(y_true, y_pred))
    mae = mean_absolute_error(y_true, y_pred)
    mape = float(np.mean(np.abs((y_true - y_pred) / (y_true + 1e-8))) * 100)
    return {"rmse": rmse, "mae": mae, "mape": mape}


def log_plot(y_true, y_pred, run_name: str) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        fig, ax = plt.subplots(figsize=(8, 4))
        ax.plot(y_true, label="actual", linewidth=1)
        ax.plot(y_pred, label="predicted", linewidth=1)
        ax.set_title(f"{run_name}: Predictions vs Actuals")
        ax.legend()
        fig.tight_layout()
        p = Path(tmp) / "pred_vs_actual.png"
        fig.savefig(p, dpi=100)
        plt.close(fig)
        mlflow.log_artifact(str(p), artifact_path="plots")


# -- Models ------------------------------------------------------------------
def train_arima(y_train, y_test) -> Dict[str, float]:
    log.info("[1/5] ARIMA ...")
    with mlflow.start_run(run_name="arima", nested=True):
        order = (5, 1, 2)
        mlflow.log_params({"order_p": order[0], "order_d": order[1], "order_q": order[2]})
        fitted = ARIMA(y_train.values, order=order).fit()
        preds = fitted.forecast(steps=len(y_test))
        m = compute_metrics(y_test.values, preds)
        mlflow.log_metrics(m)
        log_plot(y_test.values, preds, "arima")
    log.info(f"  ARIMA RMSE={m['rmse']:.2f} MAPE={m['mape']:.2f}%")
    return m


def train_ets(y_train, y_test) -> Dict[str, float]:
    log.info("[2/5] Exponential Smoothing ...")
    with mlflow.start_run(run_name="exponential_smoothing", nested=True):
        params = {"trend": "add", "seasonal": "add", "seasonal_periods": 96}
        mlflow.log_params(params)
        fitted = ExponentialSmoothing(
            y_train.values, trend=params["trend"], seasonal=params["seasonal"],
            seasonal_periods=params["seasonal_periods"],
        ).fit(optimized=True)
        preds = fitted.forecast(steps=len(y_test))
        m = compute_metrics(y_test.values, preds)
        mlflow.log_metrics(m)
        log_plot(y_test.values, preds, "exponential_smoothing")
    log.info(f"  ETS RMSE={m['rmse']:.2f} MAPE={m['mape']:.2f}%")
    return m


def train_prophet(ts_train, y_train, ts_test, y_test):
    log.info("[3/5] Prophet ...")
    try:
        from prophet import Prophet
    except ImportError:
        log.warning("  Prophet not installed — skipping.")
        return None
    with mlflow.start_run(run_name="prophet", nested=True):
        df_train = pd.DataFrame({"ds": ts_train.iloc[:, 0].values, "y": y_train.values})
        df_test = pd.DataFrame({"ds": ts_test.iloc[:, 0].values})
        params = {"changepoint_prior_scale": 0.05, "seasonality_prior_scale": 10.0,
                  "seasonality_mode": "multiplicative"}
        mlflow.log_params(params)
        mdl = Prophet(**params)
        mdl.fit(df_train)
        preds = mdl.predict(df_test)["yhat"].values
        m = compute_metrics(y_test.values, preds)
        mlflow.log_metrics(m)
        log_plot(y_test.values, preds, "prophet")
    log.info(f"  Prophet RMSE={m['rmse']:.2f} MAPE={m['mape']:.2f}%")
    return m


def train_sklearn(name, estimator, X_train, y_train, X_test, y_test, cat_cols, num_cols):
    log.info(f"Training {name} ...")
    with mlflow.start_run(run_name=name, nested=True) as run:
        pipe = Pipeline([("preprocess", build_preprocessor(cat_cols, num_cols)),
                         ("model", estimator)])
        pipe.fit(X_train, y_train)
        preds = pipe.predict(X_test)
        m = compute_metrics(y_test.values, preds)
        mlflow.log_metrics(m)
        log_plot(y_test.values, preds, name)
        sig = infer_signature(X_train, pipe.predict(X_train))
        mlflow.sklearn.log_model(sk_model=pipe, artifact_path="model",
                                 signature=sig, input_example=X_train.head(5))
        run_id = run.info.run_id
    log.info(f"  {name} RMSE={m['rmse']:.2f} MAPE={m['mape']:.2f}%")
    return m, pipe, run_id


# -- Save + register ---------------------------------------------------------
def save_outputs(model_dir: str, metrics_dir: str, all_metrics: Dict, best_name: str, best_pipe):
    Path(model_dir).mkdir(parents=True, exist_ok=True)
    Path(metrics_dir).mkdir(parents=True, exist_ok=True)
    model_path = Path(model_dir) / "model.pkl"
    with open(model_path, "wb") as f:
        pickle.dump(best_pipe, f)
    log.info(f"Saved best model ({best_name}) -> {model_path}")

    best = all_metrics[best_name]
    out = {
        "best_model": best_name,
        "val_rmse": best["rmse"], "val_mae": best["mae"], "val_mape": best["mape"],
        "all_models": all_metrics,
    }
    with open(Path(metrics_dir) / "metrics.json", "w") as f:
        json.dump(out, f, indent=2)
    log.info(f"Saved metrics.json -> {metrics_dir}")


def main() -> None:
    args = parse_args()
    log.info("=" * 55)
    log.info("TRAINING - DAM MCP Prediction (Azure ML)")
    log.info("=" * 55)

    df = load_features(args.features_dir)
    X_train, X_test, y_train, y_test, ts_train, ts_test, cat_cols, num_cols = \
        prepare_data(df, args.test_size)

    # Inside an AML job MLFLOW_TRACKING_URI is preset. Locally, resolve it from
    # the workspace. Either way we only set the experiment name here.
    from src.common.config import get_mlflow_tracking_uri
    mlflow.set_tracking_uri(get_mlflow_tracking_uri())
    mlflow.set_experiment(args.experiment)
    log.info(f"MLflow tracking: {mlflow.get_tracking_uri()} | exp: {args.experiment}")

    all_metrics: Dict[str, Dict] = {}
    pipelines: Dict[str, Pipeline] = {}
    run_ids: Dict[str, str] = {}

    # Parent run groups all 5 child runs under one training run.
    with mlflow.start_run(run_name="training_pipeline"):
        all_metrics["arima"] = train_arima(y_train, y_test)
        all_metrics["exponential_smoothing"] = train_ets(y_train, y_test)
        prophet_m = train_prophet(ts_train, y_train, ts_test, y_test)
        if prophet_m is not None:
            all_metrics["prophet"] = prophet_m

        gbr = GradientBoostingRegressor(n_estimators=300, learning_rate=0.05,
                                        max_depth=5, subsample=0.8, random_state=42)
        all_metrics["gradient_boosting"], pipelines["gradient_boosting"], run_ids["gradient_boosting"] = \
            train_sklearn("gradient_boosting", gbr, X_train, y_train, X_test, y_test, cat_cols, num_cols)

        xgb = XGBRegressor(n_estimators=300, learning_rate=0.05, max_depth=6,
                           subsample=0.8, colsample_bytree=0.8, random_state=42,
                           verbosity=0, n_jobs=1)
        all_metrics["xgboost"], pipelines["xgboost"], run_ids["xgboost"] = \
            train_sklearn("xgboost", xgb, X_train, y_train, X_test, y_test, cat_cols, num_cols)

    # Best overall by RMSE; if a univariate model wins, fall back to best sklearn
    # pipeline (only sklearn pipelines are servable as a single .pkl).
    best_name = min(all_metrics, key=lambda k: all_metrics[k]["rmse"])
    if best_name not in pipelines:
        best_name = min((k for k in all_metrics if k in pipelines),
                        key=lambda k: all_metrics[k]["rmse"])
        log.info(f"Univariate model won overall; saving best sklearn pipeline: {best_name}")
    best_pipe = pipelines[best_name]

    save_outputs(args.model_dir, args.metrics_dir, all_metrics, best_name, best_pipe)

    # Register the best model into the Azure ML Model Registry (via MLflow).
    model_uri = f"runs:/{run_ids[best_name]}/model"
    result = mlflow.register_model(model_uri, args.registered_model_name)
    log.info(f"Registered {best_name} as '{args.registered_model_name}' v{result.version}")

    log.info("=" * 55)
    for name, m in sorted(all_metrics.items(), key=lambda x: x[1]["rmse"]):
        log.info(f"  {name:22s} RMSE={m['rmse']:.2f} MAE={m['mae']:.2f} MAPE={m['mape']:.2f}%")
    log.info("=" * 55)


if __name__ == "__main__":
    main()
