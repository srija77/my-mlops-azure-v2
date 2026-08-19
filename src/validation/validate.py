"""
validate.py
===========
Azure ML pipeline component: STAGE 2 (silver -> validated) + QUALITY GATE.

Applies an expectation suite per source, splits every dataset into valid and
invalid rows, writes both, logs the pass rates as job metrics, and FAILS THE
JOB when a source drops below its minimum valid rate. Nothing downstream can
train on data that did not pass.

WHAT THIS IS A PORT OF
----------------------
The source project's `src/2_validation/5_gx_validate_local.py` (625 lines):
Great Expectations suites -> valid/invalid split -> local Postgres audit tables.
Two of those three pieces do not survive the move to Azure:

  * Postgres audit tables  -> replaced by MLflow metrics on the job + the
    rejected-rows folder, which becomes a *versioned Azure ML Data Asset*.
    Nobody has to keep a database alive to answer "what did we throw away in
    March?" -- the answer is an artifact of the run that produced it.
  * GX Data Docs           -> replaced by the AML job's metrics tab. The
    expectations below are plain pandas so the component runs in the same
    environment as everything else (no second ACR image just to validate).
    If you want GX Data Docs back for the course, register a second
    environment with `great-expectations` and swap `_check_*` for a GX suite --
    the valid/invalid contract and the gate stay identical.

What is genuinely Azure about this stage is the GATE, not the library: a failed
expectation fails the pipeline job, so `train` never starts, and the partial
outputs stay on the datastore for inspection.

VALIDATED LAYOUT WRITTEN (the contract build_features.py reads)
---------------------------------------------------------------
  dam/valid/year=YYYY/month=MM/date=YYYY-MM-DD/part-0001.parquet
  rtm/valid/      ... same ...
  weather/valid/  ... same ...
  calendar/valid/calendar.parquet

Every date-partitioned source sits exactly one directory below month=MM. That
is not cosmetic: build_features.py globs `month=MM/**/*.parquet` *without*
`recursive=True`, so `**` matches exactly one level. Flattening weather to
month=MM/part-0001.parquet makes it invisible to the feature builder.
  {dataset}/invalid/...  (same layout, the rejected rows + `_reject_reason`)
  _validation_report.json

Usage (local test):
    python src/validation/validate.py --silver-dir outputs/silver \
        --output-dir outputs/validated
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s [VALIDATE] %(message)s")
log = logging.getLogger("validate")

# A source failing this share of rows fails the pipeline. Same intent as the
# source project's expectation suites, expressed as one number per dataset.
DEFAULT_MIN_VALID_RATE = 0.95

# Physical / market plausibility bounds. A row outside these is not "unusual",
# it is wrong -- IEX MCP is capped at Rs 10,000/MWh by regulation, and Indian
# ambient temperature has never left the -20..60 C band.
MCP_MIN, MCP_MAX = 0.0, 10_000.0
MW_MAX = 1_000_000.0
TEMP_MIN, TEMP_MAX = -20.0, 60.0
HUMIDITY_MIN, HUMIDITY_MAX = 0.0, 100.0


def _reject(mask: pd.Series, reason: str, reasons: pd.Series) -> pd.Series:
    """Record the first reason each row failed for, so rejects are explainable."""
    return reasons.mask(mask & reasons.eq(""), reason)


def _split(df: pd.DataFrame, reasons: pd.Series) -> tuple[pd.DataFrame, pd.DataFrame]:
    ok = reasons.eq("")
    valid = df[ok].drop(columns=["_reject_reason"], errors="ignore")
    invalid = df[~ok].copy()
    invalid["_reject_reason"] = reasons[~ok]
    return valid, invalid


# -------------------------------------------------------------------------
# EXPECTATION SUITES -- one per source
# -------------------------------------------------------------------------
def check_market(df: pd.DataFrame, market: str) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """
    DAM/RTM expectations:
      1. Datetime present and parseable
      2. MCP present and within the regulated 0..10,000 Rs/MWh band
      3. Volume columns non-negative and below a sane ceiling
      4. Duplicate (Datetime[, Session ID]) blocks flagged -- the market clears
         each block exactly once, so a second row for one block is a scrape
         artifact, not data.
    """
    reasons = pd.Series("", index=df.index)
    mcp = "MCP (Rs/MWh) *"

    reasons = _reject(df["Datetime"].isna(), "datetime_null", reasons)
    reasons = _reject(df[mcp].isna(), "mcp_null", reasons)
    reasons = _reject(
        df[mcp].notna() & ((df[mcp] < MCP_MIN) | (df[mcp] > MCP_MAX)),
        "mcp_out_of_range",
        reasons,
    )
    for col in ["Purchase Bid (MW)", "Sell Bid (MW)", "MCV (MW)", "Final Scheduled Volume (MW)"]:
        if col in df.columns:
            reasons = _reject(
                df[col].notna() & ((df[col] < 0) | (df[col] > MW_MAX)),
                f"{col.split(' (')[0].lower().replace(' ', '_')}_out_of_range",
                reasons,
            )

    key = ["Datetime", "Session ID"] if "Session ID" in df.columns else ["Datetime"]
    dupes = df.duplicated(subset=key, keep="first")
    reasons = _reject(dupes, "duplicate_block", reasons)

    valid, invalid = _split(df, reasons)
    stats = {
        "duplicate_blocks": int(dupes.sum()),
        "unique_blocks": int(df.drop_duplicates(subset=key).shape[0]),
    }
    log.info(f"  {market.upper():8s} {len(df):6,} rows -> {len(valid):6,} valid, "
             f"{len(invalid):5,} invalid ({stats['duplicate_blocks']:,} duplicate blocks)")
    return valid, invalid, stats


def check_weather(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Weather expectations: timestamp + station present, physical ranges sane."""
    reasons = pd.Series("", index=df.index)

    reasons = _reject(df["time"].isna(), "time_null", reasons)
    reasons = _reject(df["city_name"].isna(), "city_null", reasons)
    if "temperature" in df.columns:
        reasons = _reject(
            df["temperature"].notna()
            & ((df["temperature"] < TEMP_MIN) | (df["temperature"] > TEMP_MAX)),
            "temperature_out_of_range",
            reasons,
        )
    if "humidity" in df.columns:
        reasons = _reject(
            df["humidity"].notna()
            & ((df["humidity"] < HUMIDITY_MIN) | (df["humidity"] > HUMIDITY_MAX)),
            "humidity_out_of_range",
            reasons,
        )
    for col in ["windspeed_100m", "cloud_cover", "rainfall"]:
        if col in df.columns:
            reasons = _reject(df[col].notna() & (df[col] < 0), f"{col}_negative", reasons)

    dupes = df.duplicated(subset=["city_name", "time"], keep="first")
    reasons = _reject(dupes, "duplicate_reading", reasons)

    valid, invalid = _split(df, reasons)
    stats = {"stations": int(df["city_name"].nunique()), "duplicate_readings": int(dupes.sum())}
    log.info(f"  WEATHER  {len(df):6,} rows -> {len(valid):6,} valid, {len(invalid):5,} invalid "
             f"({stats['stations']} stations)")
    return valid, invalid, stats


def check_calendar(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Calendar expectations: date present; impact numeric and in 0..10."""
    reasons = pd.Series("", index=df.index)

    reasons = _reject(df["date"].isna(), "date_null", reasons)
    if "impact" in df.columns:
        impact = pd.to_numeric(df["impact"], errors="coerce")
        reasons = _reject(impact.notna() & ((impact < 0) | (impact > 10)), "impact_out_of_range", reasons)

    valid, invalid = _split(df, reasons)
    stats = {"distinct_dates": int(df["date"].nunique())}
    log.info(f"  CALENDAR {len(df):6,} rows -> {len(valid):6,} valid, {len(invalid):5,} invalid "
             f"({stats['distinct_dates']} distinct dates)")
    return valid, invalid, stats


# -------------------------------------------------------------------------
# LOAD SILVER / WRITE VALIDATED
# -------------------------------------------------------------------------
def _load(silver: Path, pattern: str) -> pd.DataFrame:
    files = sorted(silver.glob(pattern))
    if not files:
        raise FileNotFoundError(f"No silver parquet matched {silver / pattern}")
    return pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)


def _write_partitioned(df: pd.DataFrame, root: Path, ts_col: str, by_date: bool) -> None:
    """Write the layout build_features.py globs for. Empty frames write nothing."""
    if df.empty:
        return
    ts = pd.to_datetime(df[ts_col])
    if by_date:
        keys = ts.dt.strftime("%Y-%m-%d")
        for date_str, part in df.groupby(keys):
            path = (root / f"year={date_str[:4]}" / f"month={date_str[5:7]}"
                    / f"date={date_str}" / "part-0001.parquet")
            path.parent.mkdir(parents=True, exist_ok=True)
            part.to_parquet(path, index=False, compression="snappy")
    else:
        keys = ts.dt.strftime("%Y-%m")
        for ym, part in df.groupby(keys):
            path = root / f"year={ym[:4]}" / f"month={ym[5:7]}" / "part-0001.parquet"
            path.parent.mkdir(parents=True, exist_ok=True)
            part.to_parquet(path, index=False, compression="snappy")


def _write_single(df: pd.DataFrame, path: Path) -> None:
    if df.empty:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False, compression="snappy")


def main() -> None:
    parser = argparse.ArgumentParser(description="Silver -> Validated + quality gate")
    parser.add_argument("--silver-dir", required=True, help="Ingest component output")
    parser.add_argument("--output-dir", required=True, help="Validated output folder")
    parser.add_argument("--min-valid-rate", type=float, default=DEFAULT_MIN_VALID_RATE,
                        help="Fail the job if any source falls below this valid-row share")
    parser.add_argument("--fail-on-breach", default="true",
                        help="'false' to warn instead of failing (teaching / backfill runs)")
    args = parser.parse_args()

    silver, out = Path(args.silver_dir), Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    fail_on_breach = str(args.fail_on_breach).lower() in ("true", "1", "yes")

    log.info(f"Silver    : {silver}")
    log.info(f"Validated : {out}")
    log.info(f"Gate      : min valid rate {args.min_valid_rate:.0%} "
             f"({'blocking' if fail_on_breach else 'warn-only'})")

    results: dict[str, dict] = {}

    for market in ("dam", "rtm"):
        df = _load(silver, f"{market}/year=*/month=*/date=*/*.parquet")
        valid, invalid, stats = check_market(df, market)
        _write_partitioned(valid, out / market / "valid", "Datetime", by_date=True)
        _write_partitioned(invalid, out / market / "invalid", "Datetime", by_date=True)
        results[market] = {"total": len(df), "valid": len(valid), "invalid": len(invalid), **stats}

    # Silver partitions weather by station; validated re-partitions it by date so
    # every date-partitioned source shares one layout (see the module docstring).
    weather = _load(silver, "weather/city=*/*.parquet")
    valid, invalid, stats = check_weather(weather)
    _write_partitioned(valid, out / "weather" / "valid", "time", by_date=True)
    _write_partitioned(invalid, out / "weather" / "invalid", "time", by_date=True)
    results["weather"] = {"total": len(weather), "valid": len(valid), "invalid": len(invalid), **stats}

    calendar = _load(silver, "calendar/calendar.parquet")
    valid, invalid, stats = check_calendar(calendar)
    _write_single(valid, out / "calendar" / "valid" / "calendar.parquet")
    _write_single(invalid, out / "calendar" / "invalid" / "calendar.parquet")
    results["calendar"] = {"total": len(calendar), "valid": len(valid), "invalid": len(invalid), **stats}

    # --- the gate -------------------------------------------------------
    breaches = []
    for name, r in results.items():
        r["valid_rate"] = round(r["valid"] / r["total"], 4) if r["total"] else 0.0
        if r["valid_rate"] < args.min_valid_rate:
            breaches.append(f"{name} {r['valid_rate']:.1%} < {args.min_valid_rate:.0%}")

    report = {
        "min_valid_rate": args.min_valid_rate,
        "fail_on_breach": fail_on_breach,
        "breaches": breaches,
        "passed": not breaches,
        "sources": results,
    }
    (out / "_validation_report.json").write_text(json.dumps(report, indent=2))

    try:
        import mlflow

        for name, r in results.items():
            mlflow.log_metric(f"valid_rate_{name}", r["valid_rate"])
            mlflow.log_metric(f"invalid_rows_{name}", r["invalid"])
        mlflow.log_metric("validation_passed", int(not breaches))
    except Exception as exc:  # noqa: BLE001
        log.warning(f"MLflow logging skipped: {exc}")

    log.info("-" * 62)
    for name, r in results.items():
        log.info(f"  {name:9s} {r['valid']:6,}/{r['total']:6,} valid ({r['valid_rate']:.1%})")
    log.info("-" * 62)

    if breaches:
        msg = "QUALITY GATE FAILED: " + "; ".join(breaches)
        if fail_on_breach:
            raise SystemExit(msg)
        log.warning(msg + "  (warn-only, continuing)")
    else:
        log.info("QUALITY GATE PASSED")


if __name__ == "__main__":
    main()
