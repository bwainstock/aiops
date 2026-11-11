# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false

import json
import logging

from aiops_demo_api.main import BuildMetadata, create_app
from fastapi.testclient import TestClient
from httpx2 import Response
from pytest import LogCaptureFixture, MonkeyPatch

BUILD = BuildMetadata(version="1.2.3", commit_sha="abc123def")
CLIENT = TestClient(create_app(BUILD))


def test_healthz_contract() -> None:
    response: Response = CLIENT.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "demo-api",
        "build": {"version": "1.2.3", "commit_sha": "abc123def"},
    }


def test_items_contract_is_deterministic() -> None:
    response: Response = CLIENT.get("/api/items", params={"offset": 1, "limit": 2})

    assert response.status_code == 200
    assert response.json() == {
        "items": [
            {"id": 2, "name": "compass", "price_cents": 3200},
            {"id": 3, "name": "lantern", "price_cents": 4800},
        ],
        "total": 5,
        "offset": 1,
        "limit": 2,
        "build": {"version": "1.2.3", "commit_sha": "abc123def"},
    }


def test_items_rejects_invalid_query() -> None:
    response: Response = CLIENT.get("/api/items", params={"offset": -1, "limit": 101})

    assert response.status_code == 422


def test_build_metadata_comes_from_environment(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("BUILD_VERSION", "9.8.7")
    monkeypatch.setenv("COMMIT_SHA", "fedcba987")

    with TestClient(create_app()) as client:
        response: Response = client.get("/healthz")

    assert response.json()["build"] == {
        "version": "9.8.7",
        "commit_sha": "fedcba987",
    }


def test_request_id_is_correlated_in_response_and_json_log(
    caplog: LogCaptureFixture,
) -> None:
    request_id = "manual-check-123"

    with (
        caplog.at_level(logging.INFO, logger="aiops_demo_api.http"),
        TestClient(create_app(BUILD)) as client,
    ):
        response: Response = client.get(
            "/api/items",
            headers={"X-Request-ID": request_id},
        )

    assert response.headers["X-Request-ID"] == request_id
    log_entries = [json.loads(record.getMessage()) for record in caplog.records]
    request_log = next(entry for entry in log_entries if entry.get("event") == "http_request")
    assert request_log == {
        "duration_ms": request_log["duration_ms"],
        "event": "http_request",
        "level": "info",
        "method": "GET",
        "request_id": request_id,
        "route": "/api/items",
        "service": "demo-api",
        "status_code": 200,
        "timestamp": request_log["timestamp"],
    }


def test_invalid_request_id_is_replaced() -> None:
    with TestClient(create_app(BUILD)) as client:
        response: Response = client.get(
            "/healthz",
            headers={"X-Request-ID": "not valid because it has spaces"},
        )

    generated_request_id = response.headers["X-Request-ID"]
    assert generated_request_id != "not valid because it has spaces"
    assert len(generated_request_id) == 32


def test_metrics_use_route_templates_and_bounded_labels() -> None:
    first_request_id = "bounded-label-check-1"
    second_request_id = "bounded-label-check-2"

    with TestClient(create_app(BUILD)) as client:
        client.get(
            "/api/items?offset=0&limit=1",
            headers={"X-Request-ID": first_request_id},
        )
        client.get(
            "/api/items?offset=4&limit=1",
            headers={"X-Request-ID": second_request_id},
        )
        response: Response = client.get("/metrics")

    metrics = response.text
    assert (
        'demo_http_requests_total{method="GET",route="/api/items",status_code="200"} 2.0' in metrics
    )
    assert 'demo_build_info{commit_sha="abc123def",version="1.2.3"} 1.0' in metrics
    assert first_request_id not in metrics
    assert second_request_id not in metrics
    assert "offset=" not in metrics
