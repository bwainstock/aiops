# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false

import json
import logging
from time import perf_counter

import pytest
from aiops_demo_api.main import BuildMetadata, create_app
from fastapi.testclient import TestClient
from httpx2 import Response
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind
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


def test_request_trace_is_exported_and_correlated_with_log(
    caplog: LogCaptureFixture,
) -> None:
    exporter = InMemorySpanExporter()
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(SimpleSpanProcessor(exporter))

    with (
        caplog.at_level(logging.INFO, logger="aiops_demo_api.http"),
        TestClient(create_app(BUILD, tracer_provider=tracer_provider)) as client,
    ):
        response: Response = client.get(
            "/api/items?offset=0&limit=1",
            headers={"X-Request-ID": "trace-correlation-check"},
        )

    assert response.status_code == 200
    assert tracer_provider.force_flush()
    server_span = next(
        span for span in exporter.get_finished_spans() if span.kind == SpanKind.SERVER
    )
    assert server_span.attributes is not None
    assert server_span.attributes["http.route"] == "/api/items"
    assert server_span.context is not None
    expected_trace_id = f"{server_span.context.trace_id:032x}"

    log_entries = [json.loads(record.getMessage()) for record in caplog.records]
    request_log = next(
        entry for entry in log_entries if entry.get("request_id") == "trace-correlation-check"
    )
    assert request_log["trace_id"] == expected_trace_id
    assert len(request_log["span_id"]) == 16
    tracer_provider.shutdown()


def test_controlled_error_is_reported_in_metrics() -> None:
    with TestClient(create_app(BUILD)) as client:
        failed: Response = client.get(
            "/api/items",
            headers={"X-Demo-Fault": "error"},
        )
        metrics: Response = client.get("/metrics")

    assert failed.status_code == 500
    assert failed.json() == {"detail": "controlled demo error"}
    assert (
        'demo_http_requests_total{method="GET",route="/api/items",status_code="500"} 1.0'
        in metrics.text
    )


def test_controlled_latency_delays_response(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("DEMO_LATENCY_SECONDS", "0.02")

    with TestClient(create_app(BUILD)) as client:
        started_at = perf_counter()
        response: Response = client.get(
            "/api/items",
            headers={"X-Demo-Fault": "latency"},
        )
        duration = perf_counter() - started_at

    assert response.status_code == 200
    assert duration >= 0.015


def test_invalid_latency_configuration_fails_startup(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("DEMO_LATENCY_SECONDS", "0")

    with pytest.raises(ValueError, match="DEMO_LATENCY_SECONDS must be positive"):
        create_app(BUILD)
