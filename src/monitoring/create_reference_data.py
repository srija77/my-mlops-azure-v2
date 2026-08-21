"""
create_reference_data.py
=========================
Snapshots the training feature distribution as the drift-detection *baseline*,
the Azure equivalent of the source project's
`src/8_monitoring/1_create_reference_data.py` + drift_baselines/.

Writes:
  - reference_data.parquet  — the reference feature rows (used by Azure ML Model
                              Monitoring and/or Evidently as the comparison set)
  - feature_stats.json      — per-feature mean/std/min/max for quick drift checks

Register the parquet as an AML data asset so the monitor can point at it:
    az ml data create --name dam_mcp_reference --version 1 \
        --type uri_file --path monitoring/reference_data.parquet
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s [MONITOR] %(message)s")
log = logging.getLogger("reference_data")

TARGET_COL = "dam_mcp"
TIMESTAMP_COL = "event_timestamp"
FEATURES_FILENAME = "march_2025_features.parquet"


def main() -> None:
    p = argparse.ArgumentParser(description="Create drift reference baseline")
    p.add_argument("--features-dir", required=True)
    p.add_argument("--output-dir", required=True,
                   help="Registered as dam_mcp_reference. Parquet ONLY.")
    p.add_argument("--stats-dir", default=None,
                   help="Where feature_stats.json goes. Defaults to --output-dir "
                        "for standalone use; the pipeline passes a separate "
                        "folder to keep the registered asset parquet-only.")
    args = p.parse_args()

    path = Path(args.features_dir)
    fp = path / FEATURES_FILENAME if path.is_dir() else path
    if not fp.exists():
        fp = sorted(path.glob("*.parquet"))[0]
    df = pd.read_parquet(fp)

    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    stats_dir = Path(args.stats_dir) if args.stats_dir else out
    stats_dir.mkdir(parents=True, exist_ok=True)

    # Keep everything except the entity id; the monitor compares feature + target
    # distributions between this baseline and live production inference data.
    ref = df.drop(columns=[c for c in ["block_id"] if c in df.columns])
    ref.to_parquet(out / "reference_data.parquet", index=False)

    num = ref.select_dtypes(include="number")
    stats = {
        col: {
            "mean": float(num[col].mean()),
            "std": float(num[col].std()),
            "min": float(num[col].min()),
            "max": float(num[col].max()),
        }
        for col in num.columns
    }
    with open(stats_dir / "feature_stats.json", "w") as f:
        json.dump(stats, f, indent=2)

    log.info(f"Reference baseline: {ref.shape[0]} rows, {len(stats)} numeric features")
    log.info(f"Wrote reference_data.parquet -> {out}")
    log.info(f"Wrote feature_stats.json     -> {stats_dir}")


if __name__ == "__main__":
    main()
