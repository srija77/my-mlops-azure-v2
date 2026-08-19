"""Unit tests that run without any Azure connection (used by the CI stage)."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.feature_engineering.prepare_features import prepare  # noqa: E402
from src.train.train import compute_metrics, prepare_data  # noqa: E402


def _fake_eda_frame(n: int = 200) -> pd.DataFrame:
    ts = pd.date_range("2025-03-01", periods=n, freq="15min")
    rng = np.random.default_rng(0)
    return pd.DataFrame({
        "Datetime": ts,
        "dam_mcp": rng.normal(3000, 400, n),
        "avg_temp": rng.normal(30, 5, n),
        "is_weekend": rng.integers(0, 2, n),
    })


def test_prepare_adds_feast_columns():
    out = prepare(_fake_eda_frame())
    assert "event_timestamp" in out.columns
    assert "block_id" in out.columns
    assert "Datetime" not in out.columns
    # One row per unique 15-min block.
    assert out["block_id"].is_unique


def test_compute_metrics_perfect_prediction():
    y = np.array([1.0, 2.0, 3.0, 4.0])
    m = compute_metrics(y, y)
    assert m["rmse"] == pytest.approx(0.0)
    assert m["mae"] == pytest.approx(0.0)


def test_prepare_data_time_split_is_ordered():
    df = prepare(_fake_eda_frame(120))
    X_train, X_test, y_train, y_test, ts_train, ts_test, cat, num = prepare_data(df, test_size=0.25)
    assert len(X_test) == pytest.approx(len(df) * 0.25, abs=2)
    # Test split must come strictly after train split (no leakage).
    assert ts_train.iloc[-1, 0] <= ts_test.iloc[0, 0]


def test_real_march_data_prepares_if_present():
    """If the bundled March 2025 features exist, they must be servable-shaped."""
    fp = PROJECT_ROOT / "data" / "features" / "march_2025_features.parquet"
    if not fp.exists():
        pytest.skip("March 2025 features parquet not bundled")
    df = pd.read_parquet(fp)
    assert "dam_mcp" in df.columns
    assert "event_timestamp" in df.columns
    assert len(df) > 0
