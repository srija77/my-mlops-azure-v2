"""
config.py
=========
Loads config/config.yaml and exposes it as a typed-ish dict, with environment
variable overrides. This is the single place scripts get Azure resource names,
MLflow settings, and quality gates from — so nothing is hard-coded per stage.

Env override rule: any nested key can be overridden by an UPPER_SNAKE env var
built from its path, e.g. azure.workspace_name -> AZURE_WORKSPACE_NAME.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = PROJECT_ROOT / "config" / "config.yaml"


def _apply_env_overrides(cfg: Dict[str, Any], prefix: str = "") -> None:
    for key, val in cfg.items():
        env_name = f"{prefix}{key}".upper()
        if isinstance(val, dict):
            _apply_env_overrides(val, prefix=f"{env_name}_")
        elif env_name in os.environ:
            cfg[key] = os.environ[env_name]


@lru_cache(maxsize=1)
def load_config() -> Dict[str, Any]:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    _apply_env_overrides(cfg)
    return cfg


def get_mlflow_tracking_uri() -> str:
    """Resolve the MLflow tracking URI.

    Priority:
      1. MLFLOW_TRACKING_URI env var (set automatically inside AML jobs)
      2. mlflow.tracking_uri in config.yaml (if you pasted the azureml:// URI)
      3. Derive it from the Azure ML workspace via the SDK.
    """
    env_uri = os.environ.get("MLFLOW_TRACKING_URI")
    if env_uri:
        return env_uri

    cfg = load_config()
    cfg_uri = cfg["mlflow"].get("tracking_uri")
    if cfg_uri:
        return cfg_uri

    # Fall back to asking the workspace (works locally with `az login`).
    try:
        from azure.ai.ml import MLClient
        from azure.identity import DefaultAzureCredential

        az = cfg["azure"]
        ml_client = MLClient(
            DefaultAzureCredential(),
            az["subscription_id"],
            az["resource_group"],
            az["workspace_name"],
        )
        return ml_client.workspaces.get(az["workspace_name"]).mlflow_tracking_uri
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            "Could not resolve MLflow tracking URI. Set MLFLOW_TRACKING_URI, or "
            "fill mlflow.tracking_uri in config.yaml, or `az login` first."
        ) from exc
