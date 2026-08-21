"""Unit tests for the endpoint client — the error taxonomy is the point."""

from __future__ import annotations

import httpx
import pytest

from app.aml_client import AmlClient, ScoringError, build_payload
from app.config import Settings
from app.schema import FEATURE_ORDER, SAMPLE_ROW


def make_settings(**overrides) -> Settings:
    base = dict(
        endpoint_url="https://example.invalid/score",
        endpoint_key="k",
        deployment="champion",
        timeout_seconds=5.0,
        retries=2,
        app_insights_connection_string=None,
        revision="test",
        model_name="dam_mcp_forecast",
    )
    base.update(overrides)
    return Settings(**base)


def client_with(handler, **overrides) -> AmlClient:
    transport = httpx.MockTransport(handler)
    return AmlClient(make_settings(**overrides), httpx.AsyncClient(transport=transport))


def test_build_payload_uses_schema_order_not_dict_order():
    shuffled = {k: SAMPLE_ROW[k] for k in reversed(FEATURE_ORDER)}
    payload = build_payload(shuffled)
    assert payload["input_data"]["columns"] == FEATURE_ORDER
    assert payload["input_data"]["data"][0][0] == SAMPLE_ROW[FEATURE_ORDER[0]]


def test_build_payload_names_missing_features():
    incomplete = {k: v for k, v in SAMPLE_ROW.items() if k != "avg_temp"}
    with pytest.raises(ScoringError, match="avg_temp"):
        build_payload(incomplete)


@pytest.mark.anyio
async def test_happy_path_returns_body():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"predictions": [3578.29], "model": "m", "n": 1})

    body = await client_with(handler).predict(SAMPLE_ROW)
    assert body["predictions"] == [3578.29]


@pytest.mark.anyio
async def test_http_200_with_error_body_is_a_failure():
    """score.py catches its own exceptions and returns them with HTTP 200."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"error": "columns are missing"})

    with pytest.raises(ScoringError, match="columns are missing"):
        await client_with(handler).predict(SAMPLE_ROW)


@pytest.mark.anyio
async def test_string_body_from_cli_style_response_is_parsed():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json='{"predictions": [1.5], "model": "m", "n": 1}')

    body = await client_with(handler).predict(SAMPLE_ROW)
    assert body["predictions"] == [1.5]


@pytest.mark.anyio
async def test_5xx_is_retried_then_succeeds():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503, text="upstream not ready")
        return httpx.Response(200, json={"predictions": [7.0], "model": "m", "n": 1})

    body = await client_with(handler, retries=1).predict(SAMPLE_ROW)
    assert body["predictions"] == [7.0]
    assert calls["n"] == 2


@pytest.mark.anyio
async def test_401_is_not_retried():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(401, text="unauthorized")

    with pytest.raises(ScoringError, match="401"):
        await client_with(handler, retries=3).predict(SAMPLE_ROW)
    assert calls["n"] == 1, "a bad key cannot become a good key by retrying"


@pytest.mark.anyio
async def test_deployment_header_pins_the_deployment():
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.headers)
        return httpx.Response(200, json={"predictions": [1.0], "model": "m", "n": 1})

    await client_with(handler).predict(SAMPLE_ROW)
    assert seen["azureml-model-deployment"] == "champion"


@pytest.mark.anyio
async def test_unconfigured_client_fails_clearly():
    unconfigured = AmlClient(make_settings(endpoint_url="", endpoint_key=""))
    with pytest.raises(ScoringError, match="not configured"):
        await unconfigured.predict(SAMPLE_ROW)
