# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false

import json
from datetime import timedelta
from pathlib import Path

from aiops_core.api import create_app
from aiops_core.config import Settings
from aiops_core.database import Database
from aiops_core.intake import IncidentIntake, isoformat, utc_now
from aiops_core.repository import IncidentRepository
from aiops_core.workflow import WorkflowQueue
from aiops_shared.alertmanager import AlertmanagerWebhook
from fastapi.testclient import TestClient
from httpx2 import Response

FIXTURES = Path(__file__).parent / "fixtures"
FIRING_PAYLOAD = json.loads((FIXTURES / "alertmanager-firing.json").read_text())
RESOLVED_PAYLOAD = json.loads((FIXTURES / "alertmanager-resolved.json").read_text())


def settings(database_path: Path) -> Settings:
    return Settings(
        database_path=database_path,
        worker_poll_seconds=0.01,
        job_lease_seconds=5,
        worker_processing_delay_seconds=0,
        job_max_attempts=3,
    )


def test_alert_replay_is_idempotent_and_resolution_updates_incident(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "aiops.db"

    with TestClient(create_app(settings(database_path))) as client:
        first: Response = client.post(
            "/api/v1/events/alertmanager",
            json=FIRING_PAYLOAD,
        )
        replay: Response = client.post(
            "/api/v1/events/alertmanager",
            json=FIRING_PAYLOAD,
        )
        changed_envelope = json.loads(json.dumps(FIRING_PAYLOAD))
        changed_envelope["commonAnnotations"]["summary"] = "Updated summary"
        changed_replay: Response = client.post(
            "/api/v1/events/alertmanager",
            json=changed_envelope,
        )
        incidents: Response = client.get("/api/v1/incidents")
        jobs: Response = client.get("/api/v1/jobs")
        resolved: Response = client.post(
            "/api/v1/events/alertmanager",
            json=RESOLVED_PAYLOAD,
        )
        resolved_replay: Response = client.post(
            "/api/v1/events/alertmanager",
            json=RESOLVED_PAYLOAD,
        )
        detail: Response = client.get(f"/api/v1/incidents/{first.json()['incident_ids'][0]}")

    assert first.json()["accepted_events"] == 1
    assert replay.json()["duplicate_events"] == 1
    assert changed_replay.json()["duplicate_events"] == 1
    assert len(incidents.json()) == 1
    assert len(jobs.json()) == 1
    assert resolved.json()["accepted_events"] == 1
    assert resolved_replay.json()["duplicate_events"] == 1
    assert detail.json()["incident"]["status"] == "resolved"
    assert [event["kind"] for event in detail.json()["events"]] == [
        "alert.firing",
        "alert.resolved",
    ]

    repository = IncidentRepository(Database(database_path))
    assert repository.count_rows("incidents") == 1
    assert repository.count_rows("workflow_jobs") == 1
    assert repository.count_rows("raw_events") == 3


def test_api_restart_preserves_incident_and_deployment_deduplication(
    tmp_path: Path,
) -> None:
    active_settings = settings(tmp_path / "aiops.db")
    deployment = {
        "service": "demo-api",
        "environment": "local",
        "commit_sha": "8e0df7e",
        "version": "0.1.0",
        "deployed_at": "2026-11-11T22:00:00Z",
        "metadata": {"source": "test"},
    }

    with TestClient(create_app(active_settings)) as client:
        client.post("/api/v1/events/alertmanager", json=FIRING_PAYLOAD)
        first_deployment: Response = client.post(
            "/api/v1/events/deployments",
            json=deployment,
        )

    with TestClient(create_app(active_settings)) as restarted_client:
        incidents: Response = restarted_client.get("/api/v1/incidents")
        replayed_deployment: Response = restarted_client.post(
            "/api/v1/events/deployments",
            json=deployment,
        )

    assert len(incidents.json()) == 1
    assert first_deployment.json()["duplicate"] is False
    assert replayed_deployment.json() == {
        "id": first_deployment.json()["id"],
        "duplicate": True,
    }


def test_expired_job_lease_is_recovered_without_duplicate_job(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "aiops.db")
    started_at = utc_now() + timedelta(seconds=1)
    database.initialize(applied_at=isoformat(started_at))
    intake = IncidentIntake(database, job_max_attempts=3)
    result = intake.ingest_alertmanager(AlertmanagerWebhook.model_validate(FIRING_PAYLOAD))
    queue = WorkflowQueue(database, lease_seconds=5)

    first_claim = queue.claim_next(worker_id="worker-one", now=started_at)
    early_claim = queue.claim_next(
        worker_id="worker-two",
        now=started_at + timedelta(seconds=4),
    )
    recovered_claim = queue.claim_next(
        worker_id="worker-two",
        now=started_at + timedelta(seconds=6),
    )

    assert first_claim is not None
    assert early_claim is None
    assert recovered_claim is not None
    assert recovered_claim.id == first_claim.id
    assert recovered_claim.attempts == 2

    queue.complete(
        recovered_claim,
        now=started_at + timedelta(seconds=7),
    )

    repository = IncidentRepository(database)
    detail = repository.get_incident(result.incident_ids[0])
    assert detail is not None
    assert detail.incident.workflow_state == "collecting"
    assert len(detail.jobs) == 1
    assert detail.jobs[0].state == "done"
    assert [entry.action for entry in detail.audit].count("job.lease_expired") == 1


def test_job_failures_retry_with_backoff_then_stop_at_max_attempts(
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "aiops.db")
    started_at = utc_now() + timedelta(seconds=1)
    database.initialize(applied_at=isoformat(started_at))
    intake = IncidentIntake(database, job_max_attempts=3)
    result = intake.ingest_alertmanager(AlertmanagerWebhook.model_validate(FIRING_PAYLOAD))
    queue = WorkflowQueue(database, lease_seconds=5)

    first = queue.claim_next(worker_id="worker", now=started_at)
    assert first is not None
    queue.fail(first, error="first failure", now=started_at)

    assert (
        queue.claim_next(
            worker_id="worker",
            now=started_at + timedelta(seconds=1),
        )
        is None
    )
    second = queue.claim_next(
        worker_id="worker",
        now=started_at + timedelta(seconds=3),
    )
    assert second is not None
    queue.fail(
        second,
        error="second failure",
        now=started_at + timedelta(seconds=3),
    )

    third = queue.claim_next(
        worker_id="worker",
        now=started_at + timedelta(seconds=8),
    )
    assert third is not None
    queue.fail(
        third,
        error="final failure",
        now=started_at + timedelta(seconds=8),
    )

    detail = IncidentRepository(database).get_incident(result.incident_ids[0])
    assert detail is not None
    assert detail.jobs[0].state == "failed"
    assert detail.jobs[0].attempts == 3
    assert detail.jobs[0].last_error == "final failure"
    assert [entry.action for entry in detail.audit].count("job.retry_scheduled") == 2
    assert [entry.action for entry in detail.audit].count("job.failed") == 1


def test_final_attempt_lease_expiry_marks_job_failed(tmp_path: Path) -> None:
    database = Database(tmp_path / "aiops.db")
    started_at = utc_now() + timedelta(seconds=1)
    database.initialize(applied_at=isoformat(started_at))
    intake = IncidentIntake(database, job_max_attempts=1)
    result = intake.ingest_alertmanager(AlertmanagerWebhook.model_validate(FIRING_PAYLOAD))
    queue = WorkflowQueue(database, lease_seconds=5)

    claimed = queue.claim_next(worker_id="worker", now=started_at)
    assert claimed is not None
    assert (
        queue.claim_next(
            worker_id="replacement",
            now=started_at + timedelta(seconds=6),
        )
        is None
    )

    detail = IncidentRepository(database).get_incident(result.incident_ids[0])
    assert detail is not None
    assert detail.jobs[0].state == "failed"
    assert detail.jobs[0].last_error == "lease expired after final attempt"
    assert [entry.action for entry in detail.audit].count("job.failed") == 1
