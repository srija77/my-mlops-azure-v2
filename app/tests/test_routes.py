"""Route-level tests. Health/readiness separation is the load-bearing behaviour."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import main
from app.aml_client import ScoringError
from app.schema import FEATURE_ORDER, SAMPLE_ROW


@pytest.fixture
def client() -> TestClient:
    return TestClient(main.app)


def test_health_is_ok_even_with_no_endpoint_configured(client):
    """Liveness must never depend on the endpoint, or an endpoint outage
    escalates into the platform restarting every healthy replica.

    Under test no AML_ENDPOINT_URL is set, so this exercises exactly the
    unconfigured case without mutating the frozen Settings.
    """
    assert not main.settings.configured
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_ready_reports_503_when_unconfigured(client, monkeypatch):
    monkeypatch.setattr(main, "settings", main.settings.__class__(
        endpoint_url="", endpoint_key="", deployment=None, timeout_seconds=5.0,
        retries=0, app_insights_connection_string=None, revision="t",
        model_name="dam_mcp_forecast"))
    response = client.get("/ready")
    assert response.status_code == 503
    assert response.json()["status"] == "not-ready"


def test_index_renders_every_feature_field(client):
    body = client.get("/").text
    for name in FEATURE_ORDER:
        assert f'name="{name}"' in body, f"{name} missing from the form"


def test_sample_endpoint_matches_schema(client):
    features = client.get("/api/sample").json()["features"]
    assert set(features) == set(FEATURE_ORDER)


def test_form_rejects_non_numeric_and_names_the_field(client):
    form = {name: str(value) for name, value in SAMPLE_ROW.items()}
    form["avg_temp"] = "warm"
    response = client.post("/predict", data=form)
    assert response.status_code == 400
    assert "avg_temp" in response.text


def test_form_rejects_out_of_range_flag(client):
    form = {name: str(value) for name, value in SAMPLE_ROW.items()}
    form["is_weekend"] = "7"
    response = client.post("/predict", data=form)
    assert response.status_code == 400
    assert "is_weekend" in response.text


def test_form_renders_prediction(client, monkeypatch):
    async def fake_predict(features):
        return {"predictions": [3578.29], "model": "dam_mcp_forecast", "n": 1}

    monkeypatch.setattr(main.client, "predict", fake_predict)
    form = {name: str(value) for name, value in SAMPLE_ROW.items()}
    response = client.post("/predict", data=form)
    assert response.status_code == 200
    assert "3578.29" in response.text


def test_form_surfaces_scoring_failure_as_502(client, monkeypatch):
    async def fake_predict(features):
        raise ScoringError("endpoint exploded")

    monkeypatch.setattr(main.client, "predict", fake_predict)
    form = {name: str(value) for name, value in SAMPLE_ROW.items()}
    response = client.post("/predict", data=form)
    assert response.status_code == 502
    assert "endpoint exploded" in response.text


def test_json_api_happy_path(client, monkeypatch):
    async def fake_predict(features):
        return {"predictions": [42.0], "model": "dam_mcp_forecast", "n": 1}

    monkeypatch.setattr(main.client, "predict", fake_predict)
    response = client.post("/api/predict", json={"features": SAMPLE_ROW})
    assert response.status_code == 200
    assert response.json()["prediction"] == 42.0


def test_json_api_accepts_bare_feature_object(client, monkeypatch):
    async def fake_predict(features):
        return {"predictions": [1.0], "model": "m", "n": 1}

    monkeypatch.setattr(main.client, "predict", fake_predict)
    assert client.post("/api/predict", json=SAMPLE_ROW).status_code == 200
