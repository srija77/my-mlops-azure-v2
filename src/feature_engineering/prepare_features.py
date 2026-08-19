"""
prepare_features.py
===================
Azure ML pipeline component: STAGE 1 (data prep).

Reads the March 2025 EDA feature Parquet (an Azure ML Data Asset, mounted as a
folder), deduplicates to one row per 15-min block, adds the Feast-style
`event_timestamp` + `block_id` columns, and writes a clean Parquet to the
component's output folder — which the next component consumes.

This is the Azure ML equivalent of the source project's
`src/3_feature_engineering/2_prepare_feast_data.py`, refactored to take
--input-dir / --output-dir so it plugs into an AML pipeline as a component.

Usage (local test):
    python src/feature_engineering/prepare_features.py \
        --input-dir data --output-dir outputs/features
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s [PREP] %(message)s")
log = logging.getLogger("prepare_features")

INPUT_FILENAME = "march_2025_prepared.parquet"
OUTPUT_FILENAME = "march_2025_features.parquet"


def _resolve_input(input_path: Path) -> Path:
    """Accept either a file or a folder-mounted data asset."""
    if input_path.is_file():
        return input_path
    candidate = input_path / INPUT_FILENAME
    if candidate.exists():
        return candidate
    # Fall back to the first parquet in the folder.
    parquets = sorted(input_path.glob("*.parquet"))
    if not parquets:
        raise FileNotFoundError(f"No parquet found under {input_path}")
    return parquets[0]


def prepare(df: pd.DataFrame) -> pd.DataFrame:
    log.info(f"Loaded: {df.shape[0]} rows, {df.shape[1]} columns")

    # Feast requires an event_timestamp column.
    df["event_timestamp"] = pd.to_datetime(df["Datetime"])
    df = df.drop(columns=["Datetime"])

    # Entity key: block_id = unique 15-min slot identifier, e.g. "2025-03-15T14:30".
    df["block_id"] = df["event_timestamp"].dt.strftime("%Y-%m-%dT%H:%M")

    # Deduplicate to one row per block_id — average numeric cols, first for others.
    numeric_cols = df.select_dtypes(include="number").columns.tolist()
    non_numeric_cols = [c for c in df.columns if c not in numeric_cols and c != "block_id"]
    agg_dict = {c: "mean" for c in numeric_cols}
    agg_dict.update({c: "first" for c in non_numeric_cols})
    df = df.groupby("block_id", as_index=False).agg(agg_dict)
    log.info(f"After dedup: {df.shape[0]} rows (one per 15-min block)")

    return df.sort_values("event_timestamp").reset_index(drop=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare March 2025 features for Feast/AML")
    parser.add_argument("--input-dir", required=True, help="Folder or file: EDA prepared parquet")
    parser.add_argument("--output-dir", required=True, help="Folder to write the features parquet")
    args = parser.parse_args()

    src = _resolve_input(Path(args.input_dir))
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / OUTPUT_FILENAME

    df = prepare(pd.read_parquet(src))
    df.to_parquet(out_path, index=False)

    log.info(f"Saved: {out_path}")
    log.info(f"Final shape: {df.shape}")
    log.info(f"Date range: {df['event_timestamp'].min()} to {df['event_timestamp'].max()}")


if __name__ == "__main__":
    main()
