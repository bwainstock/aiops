# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false

from datetime import UTC, datetime

from aiops_alert_sink.main import AlertmanagerWebhook, EventStore, create_app
from fastapi.testclient import TestClient
from httpx2 import Response

FIRING_PAYLOAD = {
    "version": "4",
    "groupKey": '{}:{alertname="HighErrorRate"}',
    "truncatedAlerts": 0,
    "status": "firing",
    "receiver": "capture",
    "groupLabels": {"alertname": "HighErrorRate"},
    "commonLabels": {
        "alertname": "HighErrorRate",
        "severity": "warning",
        "service": "demo-api",
    },
    "commonAnnotations": {
        "summary": "Demo API error rate is high",
    },
    "externalURL": "http://alertmanager:9093",
    "alerts": [
        {
            "status": "firing",
            "labels": {
                "alertname": "HighErrorRate",
                "severity": "warning",
                "service": "demo-api",
            },
            "annotations": {
                "summary": "Demo API error rate is high",
            },
            "startsAt": "2026-11-11T22:00:00Z",
            "endsAt": "0001-01-01T00:00:00Z",
            "generatorURL": "http://prometheus:9090/graph",
            "fingerprint": "abc123",
        }
    ],
}


def test_capture_list_filter_and_clear() -> None:
    store = EventStore()

    with TestClient(create_app(store)) as client:
        captured: Response = client.post("/api/v1/alerts", json=FIRING_PAYLOAD)
        all_events: Response = client.get("/api/v1/events")
        filtered_events: Response = client.get(
            "/api/v1/events",
            params={"alertname": "HighErrorRate", "status": "firing"},
        )
        excluded_events: Response = client.get(
            "/api/v1/events",
            params={"alertname": "TargetDown"},
        )
        deleted: Response = client.delete("/api/v1/events")
        empty: Response = client.get("/api/v1/events")

    assert captured.status_code == 202
    assert captured.json() == {"id": 1}
    assert len(all_events.json()) == 1
    assert len(filtered_events.json()) == 1
    assert excluded_events.json() == []
    assert deleted.json() == {"deleted": 1}
    assert empty.json() == []


def test_invalid_alertmanager_payload_is_rejected() -> None:
    with TestClient(create_app()) as client:
        response: Response = client.post(
            "/api/v1/alerts",
            json={"status": "firing"},
        )

    assert response.status_code == 422


def test_resolved_alertmanager_payload_is_accepted() -> None:
    webhook = AlertmanagerWebhook.model_validate(FIRING_PAYLOAD)
    resolved_alert = webhook.alerts[0].model_copy(
        update={
            "status": "resolved",
            "endsAt": datetime(2026, 11, 11, 22, 5, tzinfo=UTC),
        }
    )
    payload = webhook.model_copy(
        update={
            "status": "resolved",
            "alerts": [resolved_alert],
        }
    ).model_dump(mode="json")

    with TestClient(create_app()) as client:
        captured: Response = client.post("/api/v1/alerts", json=payload)
        events: Response = client.get(
            "/api/v1/events",
            params={"status": "resolved"},
        )

    assert captured.status_code == 202
    assert events.json()[0]["payload"]["alerts"][0]["endsAt"] == "2026-11-11T22:05:00Z"
