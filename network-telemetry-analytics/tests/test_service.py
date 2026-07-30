from fastapi.testclient import TestClient

from app.main import app


def test_service_info_is_available_without_database_access() -> None:
    with TestClient(app) as client:
        response = client.get("/")
    assert response.status_code == 200
    assert response.json()["service"] == "Network Telemetry Analytics Service"


def test_openapi_exposes_telemetry_and_analytics_routes() -> None:
    with TestClient(app) as client:
        response = client.get("/openapi.json")
    paths = response.json()["paths"]
    assert "/api/v1/telemetry" in paths
    assert "/api/v1/analytics/anomalies" in paths


def test_prometheus_metrics_are_exposed() -> None:
    with TestClient(app) as client:
        response = client.get("/metrics")
    assert response.status_code == 200
    assert "telemetry_api_requests_total" in response.text
