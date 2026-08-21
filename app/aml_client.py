"""
aml_client.py
=============
Thin async client for the Azure ML managed online endpoint.

Why a wrapper rather than calling httpx inline: the endpoint has three distinct
failure modes that the UI must tell apart, and only one of them is the app's
fault.

  1. Transport failure  — timeout, DNS, TLS, connection reset. Retryable.
  2. HTTP error         — 401 (bad key), 404 (no deployment), 424/5xx (the
                          scoring container crashed). 5xx is retryable, 4xx is not.
  3. Scoring error      — HTTP 200 with {"error": "..."} in the body, because
                          score.py catches its own exceptions and returns them.
                          Never retryable; retrying a malformed row just fails
                          identically three times and triples the latency.

Case 3 is the one that bites: a naive client treats HTTP 200 as success and shows
the user "prediction: None".
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from .config import Settings
from .schema import FEATURE_ORDER

log = logging.getLogger("app.aml")

# Retrying a 4xx just re-sends the same rejected request; only these can change.
RETRYABLE_STATUS = frozenset({408, 429, 500, 502, 503, 504})


class ScoringError(RuntimeError):
    """The endpoint was reached but did not return a usable prediction."""

    def __init__(self, message: str, *, status: int | None = None, retryable: bool = False):
        super().__init__(message)
        self.status = status
        self.retryable = retryable


def build_payload(features: dict[str, float]) -> dict[str, Any]:
    """Shape a feature dict into the endpoint's tabular contract.

    Column order comes from FEATURE_ORDER, never from dict iteration order, so a
    caller that builds its dict in a different order still scores correctly.
    """
    missing = [c for c in FEATURE_ORDER if c not in features]
    if missing:
        raise ScoringError(f"Missing required feature(s): {', '.join(missing)}")
    return {
        "input_data": {
            "columns": list(FEATURE_ORDER),
            "data": [[float(features[c]) for c in FEATURE_ORDER]],
        }
    }


class AmlClient:
    """Calls the scoring endpoint, with bounded retries on transient failures."""

    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None):
        self._settings = settings
        self._client = client

    @property
    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._settings.endpoint_key}",
        }
        # Pins the request to one deployment; without it the endpoint honours the
        # traffic split, which makes a blue/green comparison non-deterministic.
        if self._settings.deployment:
            headers["azureml-model-deployment"] = self._settings.deployment
        return headers

    async def predict(self, features: dict[str, float]) -> dict[str, Any]:
        if not self._settings.configured:
            raise ScoringError(
                "The scoring endpoint is not configured. Set AML_ENDPOINT_URL and "
                "AML_ENDPOINT_KEY."
            )

        payload = build_payload(features)
        attempts = self._settings.retries + 1
        last: ScoringError | None = None

        for attempt in range(1, attempts + 1):
            try:
                return await self._attempt(payload)
            except ScoringError as exc:
                last = exc
                if not exc.retryable or attempt == attempts:
                    raise
                backoff = 0.5 * (2 ** (attempt - 1))
                log.warning(
                    "scoring attempt %d/%d failed (%s); retrying in %.1fs",
                    attempt, attempts, exc, backoff,
                )
                await asyncio.sleep(backoff)

        raise last or ScoringError("Scoring failed for an unknown reason.")

    async def _attempt(self, payload: dict[str, Any]) -> dict[str, Any]:
        timeout = httpx.Timeout(self._settings.timeout_seconds)
        try:
            if self._client is not None:
                response = await self._client.post(
                    self._settings.endpoint_url, json=payload,
                    headers=self._headers, timeout=timeout,
                )
            else:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    response = await client.post(
                        self._settings.endpoint_url, json=payload, headers=self._headers
                    )
        except httpx.TimeoutException as exc:
            raise ScoringError(
                f"The endpoint did not respond within {self._settings.timeout_seconds:.0f}s.",
                retryable=True,
            ) from exc
        except httpx.HTTPError as exc:
            raise ScoringError(f"Could not reach the endpoint: {exc}", retryable=True) from exc

        return self._parse(response)

    @staticmethod
    def _parse(response: httpx.Response) -> dict[str, Any]:
        if response.status_code >= 400:
            detail = response.text[:400].strip() or response.reason_phrase
            raise ScoringError(
                f"Endpoint returned HTTP {response.status_code}: {detail}",
                status=response.status_code,
                retryable=response.status_code in RETRYABLE_STATUS,
            )

        try:
            body = response.json()
        except ValueError as exc:
            raise ScoringError("Endpoint returned a non-JSON body.") from exc

        # az ml online-endpoint invoke returns the score payload as a JSON *string*;
        # the raw HTTPS route returns an object. Accept both.
        if isinstance(body, str):
            import json as _json
            try:
                body = _json.loads(body)
            except ValueError as exc:
                raise ScoringError("Endpoint returned an unparseable body.") from exc

        if not isinstance(body, dict):
            raise ScoringError("Endpoint returned an unexpected payload shape.")

        # score.py swallows its own exceptions and returns them with HTTP 200.
        if "error" in body:
            raise ScoringError(f"The model rejected the request: {body['error']}")

        predictions = body.get("predictions")
        if not isinstance(predictions, list) or not predictions:
            raise ScoringError("Endpoint response contained no predictions.")

        return body
