"""
config.py
=========
Environment-driven settings for the inference web app.

Everything the app needs to reach the scoring endpoint arrives as an environment
variable, so the same image runs locally, in CI, and in Azure Container Apps with
no rebuild. The endpoint key is a secret: it is injected as a Container Apps
secret backed by Key Vault, never baked into the image and never logged.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


class ConfigError(RuntimeError):
    """Raised at startup when required configuration is absent."""


def _flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    endpoint_url: str
    endpoint_key: str
    deployment: str | None
    timeout_seconds: float
    retries: int
    app_insights_connection_string: str | None
    revision: str
    model_name: str

    @property
    def configured(self) -> bool:
        return bool(self.endpoint_url and self.endpoint_key)

    def redacted(self) -> dict[str, object]:
        """Safe-to-log view. Never include the key itself."""
        return {
            "endpoint_url": self.endpoint_url or "(unset)",
            "endpoint_key": "set" if self.endpoint_key else "(unset)",
            "deployment": self.deployment or "(default traffic split)",
            "timeout_seconds": self.timeout_seconds,
            "retries": self.retries,
            "revision": self.revision,
            "app_insights": bool(self.app_insights_connection_string),
        }


def load_settings() -> Settings:
    """Read settings from the environment.

    Deliberately does not raise on missing endpoint config. The container must
    still start and serve /health so the platform can distinguish "misconfigured"
    from "crashed" — a container that exits on a missing variable shows up as a
    crash loop, which sends you looking for the wrong bug. /ready reports the
    misconfiguration instead, and the UI renders a banner rather than a stack
    trace.
    """
    return Settings(
        endpoint_url=os.environ.get("AML_ENDPOINT_URL", "").strip(),
        endpoint_key=os.environ.get("AML_ENDPOINT_KEY", "").strip(),
        deployment=(os.environ.get("AML_DEPLOYMENT") or "").strip() or None,
        timeout_seconds=float(os.environ.get("REQUEST_TIMEOUT_SECONDS", "30")),
        retries=int(os.environ.get("REQUEST_RETRIES", "2")),
        app_insights_connection_string=(
            os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING") or None
        ),
        revision=os.environ.get("APP_REVISION", "dev"),
        model_name=os.environ.get("MODEL_NAME", "dam_mcp_forecast"),
    )
