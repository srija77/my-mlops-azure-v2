"""
promote.py
==========
Azure ML pipeline component: STAGE 4 (promote).

Compares the latest registered model version against the current production
model (the one carrying the `champion` alias) in the Azure ML Model Registry.
If the latest version passed evaluation AND beats production on eval_rmse, it
gets the `champion` alias and a model_version.json is written to the output —
that file is the CD trigger (a change to it drives the endpoint deployment).

Mirrors src/6_promotion/1_promote_model.py, using MLflow registry aliases
against the Azure ML workspace. (Azure ML's registry supports MLflow aliases;
the legacy stage transitions used by the source project are omitted because AML
treats aliases as the source of truth.)
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import mlflow
import pandas as pd
from mlflow import MlflowClient

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [PROMOTE] %(message)s")
log = logging.getLogger("promote")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Promote best model to production")
    p.add_argument("--report-dir", required=True, help="Folder for promote_report.json + model_version.json")
    # Ordering-only edge: forces this step to run after evaluate (which tags the
    # model version this script gates on). The path itself is not read.
    p.add_argument("--eval-signal", default=None, help=argparse.SUPPRESS)
    p.add_argument("--registered-model-name", default="dam_mcp_forecast")
    p.add_argument("--production-alias", default="champion")
    p.add_argument("--metric", default="eval_rmse")
    p.add_argument("--direction", default="lower", choices=["lower", "higher"])
    return p.parse_args()


def latest_version(client, name):
    """
    Return the highest-numbered version, re-fetched by id.

    `search_model_versions` against the Azure ML MLflow registry returns version
    stubs whose `.tags` are not always populated — unlike the OSS MLflow server,
    where the search response carries them. Gating on `latest.tags["passed_eval"]`
    straight off the search result therefore fails open-loop: a model that passed
    evaluation looks untagged, promote exits 1, and the pipeline fails with the
    model sitting in the registry correctly tagged the whole time.
    `get_model_version` is the authoritative read, so always round-trip through it.
    """
    versions = client.search_model_versions(f"name='{name}'")
    if not versions:
        return None
    newest = max(versions, key=lambda v: int(v.version))
    return client.get_model_version(name, newest.version)


def production_version(client, name, alias):
    try:
        return client.get_model_version_by_alias(name, alias)
    except mlflow.exceptions.MlflowException:
        return None


def eval_metric(client, run_id, key):
    return client.get_run(run_id).data.metrics.get(key)


def is_better(cand, prod, direction):
    return cand < prod if direction == "lower" else cand > prod


def write_report(report_dir, **fields):
    Path(report_dir).mkdir(parents=True, exist_ok=True)
    with open(Path(report_dir) / "promote_report.json", "w") as f:
        json.dump(fields, f, indent=2)
    log.info(f"promote_report.json -> {report_dir} (action={fields.get('action')})")


def write_version_file(report_dir, client, name, version, alias):
    mv = client.get_model_version(name, version)
    run = client.get_run(mv.run_id)
    info = {
        "model_name": name, "model_version": str(version), "alias": alias,
        "run_id": run.info.run_id, "run_name": run.info.run_name,
        "eval_rmse": run.data.metrics.get("eval_rmse"),
        "eval_mae": run.data.metrics.get("eval_mae"),
        "eval_mape": run.data.metrics.get("eval_mape"),
        "promoted_at": pd.Timestamp.now(tz="UTC").isoformat(),
    }
    with open(Path(report_dir) / "model_version.json", "w") as f:
        json.dump(info, f, indent=2)
    log.info(f"model_version.json written (CD trigger) for v{version}")


def promote(client, name, version, alias, report_dir):
    client.set_registered_model_alias(name, alias, version)
    log.info(f"Set alias '{alias}' on v{version}")
    write_version_file(report_dir, client, name, version, alias)


def main() -> None:
    args = parse_args()
    from src.common.config import get_mlflow_tracking_uri
    mlflow.set_tracking_uri(get_mlflow_tracking_uri())
    client = MlflowClient()
    name, alias = args.registered_model_name, args.production_alias

    latest = latest_version(client, name)
    if latest is None:
        log.error(f"No versions for '{name}'.")
        sys.exit(1)
    log.info(f"Latest: v{latest.version}")

    if latest.tags.get("passed_eval", "").strip() != "True":
        write_report(args.report_dir, action="skipped",
                     reason="latest failed evaluation (passed_eval != True)",
                     latest_version=latest.version)
        log.warning("Latest version did not pass eval — skipping promotion.")
        sys.exit(1)

    prod = production_version(client, name, alias)
    if prod is None:
        log.info("No production model yet — promoting latest directly.")
        promote(client, name, latest.version, alias, args.report_dir)
        write_report(args.report_dir, action="promoted", reason="first production model",
                     latest_version=latest.version)
        return

    if str(prod.version) == str(latest.version):
        log.info("Latest is already champion. Nothing to do.")
        write_report(args.report_dir, action="no_change",
                     reason="latest already champion", latest_version=latest.version,
                     production_version=prod.version)
        return

    cand = eval_metric(client, latest.run_id, args.metric)
    incumbent = eval_metric(client, prod.run_id, args.metric)
    if cand is None or incumbent is None:
        write_report(args.report_dir, action="skipped",
                     reason=f"metric '{args.metric}' missing",
                     latest_version=latest.version, production_version=prod.version)
        sys.exit(1)

    log.info(f"Compare {args.metric} ({args.direction} better): "
             f"latest v{latest.version}={cand:.4f} vs prod v{prod.version}={incumbent:.4f}")

    if is_better(cand, incumbent, args.direction):
        promote(client, name, latest.version, alias, args.report_dir)
        write_report(args.report_dir, action="promoted",
                     reason=f"{args.metric} improved: {cand:.4f} vs {incumbent:.4f}",
                     latest_version=latest.version, production_version=prod.version)
        log.info(f"PROMOTED v{latest.version} -> '{alias}'")
    else:
        write_report(args.report_dir, action="not_promoted",
                     reason=f"{args.metric} not better: {cand:.4f} vs {incumbent:.4f}",
                     latest_version=latest.version, production_version=prod.version)
        log.info(f"NOT promoted — v{prod.version} stays champion.")


if __name__ == "__main__":
    main()
