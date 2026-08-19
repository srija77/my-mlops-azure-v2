"""
ingest.py
=========
Azure ML pipeline component: STAGE 1 (bronze -> silver).

Reads the `energy_raw` Data Asset (bronze, mounted as a read-only folder) and
writes cleaned, typed, partitioned Parquet to the component's output folder
(silver) -- which the validation component consumes.

WHAT THIS IS A PORT OF
----------------------
The source project's `src/1_ingestion/*.py` scripts do two jobs at once: they
*scrape* IEX India with Selenium AND clean the result. Only the second half
belongs in an Azure ML job. Scraping is a scheduled data-movement concern --
Azure Function / Data Factory landing files in Blob -- not something a training
cluster should do (no browser, no retry across node scale-down, and it makes the
pipeline non-reproducible). So this component starts from bronze files the
landing job already delivered, and is deterministic: same input, same output.

BRONZE LAYOUT EXPECTED (as registered in `energy_raw`)
------------------------------------------------------
  dam/year=YYYY/month=MM/date=YYYY-MM-DD/dam.csv
  rtm/year=YYYY/month=MM/date=YYYY-MM-DD/rtm.csv
  weather/{City_State}.csv                       (hourly, one file per station)
  calendar/calendar.csv                          (daily)
  generation/dgr_master.csv                      (daily regional generation)

SILVER LAYOUT WRITTEN
---------------------
  dam/year=YYYY/month=MM/date=YYYY-MM-DD/part-0001.parquet
  rtm/year=YYYY/month=MM/date=YYYY-MM-DD/part-0001.parquet
  weather/city={City_State}/part-0001.parquet
  calendar/calendar.parquet
  generation/generation.parquet
  _ingest_summary.json                           (audit record, per source)

Every silver row carries the same three lineage columns the source project
added -- `ingestion_date`, `source_file`, `pipeline_run_id` -- so a bad row can
always be traced back to the file it came from.

Usage (local test):
    python src/ingestion/ingest.py --raw-dir data/raw --output-dir outputs/silver
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s [INGEST] %(message)s")
log = logging.getLogger("ingest")

# Lineage columns stamped onto every silver row.
META_COLS = ["ingestion_date", "source_file", "pipeline_run_id"]

# Numeric columns shared by both market sources (everything else stays as read).
MARKET_NUMERIC = [
    "Purchase Bid (MW)",
    "Sell Bid (MW)",
    "MCV (MW)",
    "Final Scheduled Volume (MW)",
    "MCP (Rs/MWh) *",
]


def _stamp(df: pd.DataFrame, source_file: Path, run_id: str) -> pd.DataFrame:
    """Add the three lineage columns. Same contract as the source project."""
    df = df.copy()
    df["ingestion_date"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    df["source_file"] = source_file.name
    df["pipeline_run_id"] = run_id
    return df


def _write(df: pd.DataFrame, path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False, compression="snappy")
    return len(df)


def _dedup(df: pd.DataFrame, keys: list[str]) -> tuple[pd.DataFrame, int]:
    """
    Drop repeated observations of the same event. Deduplication is a *cleaning*
    concern, so it happens here in silver rather than in the validation gate --
    a duplicated row is not a data-quality breach the pipeline should stop for,
    it is a scrape artifact with nothing to decide. The count is reported so the
    artifact stays visible: the March 2025 DAM files carry every 15-min block
    exactly twice (192 rows/day for 96 blocks), byte-identical.
    """
    keys = [k for k in keys if k in df.columns]
    if not keys:
        return df, 0
    before = len(df)
    df = df.drop_duplicates(subset=keys, keep="first")
    return df, before - len(df)


# -------------------------------------------------------------------------
# DAM / RTM -- Hive-partitioned daily CSVs
# -------------------------------------------------------------------------
def ingest_market(raw: Path, out: Path, market: str, run_id: str) -> dict:
    """dam|rtm: one CSV per trading day -> one Parquet per trading day."""
    src_root = raw / market
    csvs = sorted(src_root.glob("year=*/month=*/date=*/*.csv"))
    if not csvs:
        raise FileNotFoundError(f"No {market.upper()} CSVs under {src_root}")

    # The market clears each block once; RTM additionally splits a block across
    # sessions, so its identity is (Datetime, Session ID).
    dedup_keys = ["Datetime", "Session ID"] if market == "rtm" else ["Datetime"]

    rows = dropped = 0
    for csv in csvs:
        # date=YYYY-MM-DD is the parent dir; keep the partitioning as-is.
        date_str = csv.parent.name.split("=", 1)[1]
        df = pd.read_csv(csv)
        df["Datetime"] = pd.to_datetime(df["Datetime"], errors="coerce")
        for col in MARKET_NUMERIC:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.dropna(subset=["Datetime"]).sort_values("Datetime")
        df, n_dropped = _dedup(df, dedup_keys)
        dropped += n_dropped
        df = _stamp(df, csv, run_id)

        part = (
            out / market / f"year={date_str[:4]}" / f"month={date_str[5:7]}"
            / f"date={date_str}" / "part-0001.parquet"
        )
        rows += _write(df, part)

    log.info(f"  {market.upper():10s} {len(csvs):3d} day files -> {rows:,} rows "
             f"({dropped:,} duplicates dropped)")
    return {"source": market, "files": len(csvs), "rows": rows, "duplicates_dropped": dropped}


# -------------------------------------------------------------------------
# WEATHER -- one hourly CSV per station, partitioned by city
# -------------------------------------------------------------------------
def ingest_weather(raw: Path, out: Path, run_id: str) -> dict:
    csvs = sorted((raw / "weather").glob("*.csv"))
    if not csvs:
        raise FileNotFoundError(f"No weather CSVs under {raw / 'weather'}")

    rows = dropped = 0
    for csv in csvs:
        city = csv.stem                       # e.g. Chennai_Tamil_Nadu
        df = pd.read_csv(csv)
        df["time"] = pd.to_datetime(df["time"], errors="coerce")
        df = df.dropna(subset=["time"]).sort_values("time")
        df["city_name"] = city                # entity key the feature builder joins on
        df, n_dropped = _dedup(df, ["city_name", "time"])
        dropped += n_dropped
        df = _stamp(df, csv, run_id)
        rows += _write(df, out / "weather" / f"city={city}" / "part-0001.parquet")

    log.info(f"  WEATHER    {len(csvs):3d} stations  -> {rows:,} rows "
             f"({dropped:,} duplicates dropped)")
    return {"source": "weather", "files": len(csvs), "rows": rows, "duplicates_dropped": dropped}


# -------------------------------------------------------------------------
# CALENDAR -- one daily CSV (dd-mm-YYYY dates)
# -------------------------------------------------------------------------
def ingest_calendar(raw: Path, out: Path, run_id: str) -> dict:
    csv = raw / "calendar" / "calendar.csv"
    if not csv.exists():
        raise FileNotFoundError(f"Missing {csv}")

    df = pd.read_csv(csv)
    # Source is dd-mm-YYYY; normalise to a real timestamp so downstream joins work.
    df["date"] = pd.to_datetime(df["date"], format="%d-%m-%Y", errors="coerce")
    df = df.dropna(subset=["date"]).sort_values("date")
    df = _stamp(df, csv, run_id)
    rows = _write(df, out / "calendar" / "calendar.parquet")

    log.info(f"  CALENDAR     1 file      -> {rows:,} rows")
    return {"source": "calendar", "files": 1, "rows": rows}


# -------------------------------------------------------------------------
# GENERATION -- daily regional generation master (parsed from DGR PDFs upstream)
# -------------------------------------------------------------------------
def ingest_generation(raw: Path, out: Path, run_id: str) -> dict:
    """
    Landed and versioned but NOT yet consumed by the model -- the current feature
    set has no generation columns. Kept in silver so supply-side features
    (deviation_pct, fuel-mix share) can be added without re-ingesting.
    """
    csv = raw / "generation" / "dgr_master.csv"
    if not csv.exists():
        log.warning(f"  GENERATION   skipped (no {csv})")
        return {"source": "generation", "files": 0, "rows": 0, "skipped": True}

    df = pd.read_csv(csv)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date"]).sort_values(["date", "region", "fuel_type"])
    df = _stamp(df, csv, run_id)
    rows = _write(df, out / "generation" / "generation.parquet")

    log.info(f"  GENERATION   1 file      -> {rows:,} rows  (landed, not yet modelled)")
    return {"source": "generation", "files": 1, "rows": rows}


def main() -> None:
    parser = argparse.ArgumentParser(description="Bronze -> Silver ingestion (Azure ML component)")
    parser.add_argument("--raw-dir", required=True, help="Mounted `energy_raw` data asset")
    parser.add_argument("--output-dir", required=True, help="Silver output folder")
    args = parser.parse_args()

    raw, out = Path(args.raw_dir), Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)

    # Prefer the AML run id so silver rows point back at the exact job.
    run_id = os.environ.get("AZUREML_RUN_ID") or str(uuid.uuid4())
    log.info(f"Bronze : {raw}")
    log.info(f"Silver : {out}")
    log.info(f"Run id : {run_id}")

    summary = [
        ingest_market(raw, out, "dam", run_id),
        ingest_market(raw, out, "rtm", run_id),
        ingest_weather(raw, out, run_id),
        ingest_calendar(raw, out, run_id),
        ingest_generation(raw, out, run_id),
    ]

    total = sum(s["rows"] for s in summary)
    report = {
        "pipeline_run_id": run_id,
        "ingested_at": datetime.now(timezone.utc).isoformat(),
        "total_rows": total,
        "sources": summary,
    }
    (out / "_ingest_summary.json").write_text(json.dumps(report, indent=2))
    log.info(f"Done. {total:,} silver rows across {len(summary)} sources.")

    # Metrics land on the AML job so a bad ingest is visible without log-diving.
    try:
        import mlflow

        mlflow.log_metric("ingest_total_rows", total)
        for s in summary:
            mlflow.log_metric(f"ingest_rows_{s['source']}", s["rows"])
            if "duplicates_dropped" in s:
                mlflow.log_metric(f"ingest_dupes_{s['source']}", s["duplicates_dropped"])
    except Exception as exc:  # noqa: BLE001 - never fail ingestion on telemetry
        log.warning(f"MLflow logging skipped: {exc}")


if __name__ == "__main__":
    main()
