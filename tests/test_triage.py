# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from aiops_core.api import create_app
from aiops_core.config import Settings
from aiops_core.triage_models import (
    EvidenceBundle,
    EvidenceItem,
    EvidenceSource,
    SuspectedCause,
    TriageHypothesis,
    TriageReview,
    TriageSubmission,
)
from aiops_core.triage_validation import (
    evidence_bundle_digest,
    evidence_item_digest,
    validate_hypothesis,
    validate_review,
)
from fastapi.testclient import TestClient
from httpx2 import Response

FIXTURES = Path(__file__).parent / "fixtures"
FIRING_PAYLOAD = json.loads((FIXTURES / "alertmanager-firing.json").read_text())


def settings(database_path: Path) -> Settings:
    return Settings(
        database_path=database_path,
        worker_poll_seconds=0.01,
        job_lease_seconds=5,
        worker_processing_delay_seconds=0,
        job_max_attempts=3,
    )


def evidence_item(
    *,
    item_id: str,
    source: EvidenceSource,
    kind: str,
    observed_at: datetime,
    content: dict[str, object],
) -> EvidenceItem:
    item = EvidenceItem(
        id=item_id,
        source=source,
        kind=kind,
        observed_at=observed_at,
        content=content,
        digest="pending",
    )
    return item.model_copy(update={"digest": evidence_item_digest(item)})


def evidence_bundle(*, error_ratio: float = 0.9, late_deployment: bool = False) -> EvidenceBundle:
    alert_time = datetime(2026, 11, 11, 22, 0, tzinfo=UTC)
    deployment_time = (
        alert_time + timedelta(minutes=1) if late_deployment else alert_time - timedelta(minutes=1)
    )
    items = [
        evidence_item(
            item_id="ev-alert",
            source="alertmanager",
            kind="alert.firing",
            observed_at=alert_time,
            content={"alert": {"status": "firing"}},
        ),
        evidence_item(
            item_id="ev-ratio",
            source="prometheus",
            kind="error_ratio",
            observed_at=alert_time + timedelta(seconds=10),
            content={"value": error_ratio},
        ),
        evidence_item(
            item_id="ev-deploy",
            source="deployment",
            kind="deployment.latest",
            observed_at=deployment_time,
            content={"commit_sha": "abc1234"},
        ),
    ]
    bundle = EvidenceBundle(
        id="bundle-1",
        incident_id="incident-1",
        collected_at=alert_time + timedelta(minutes=2),
        window_start=alert_time - timedelta(minutes=2),
        window_end=alert_time + timedelta(minutes=2),
        items=items,
        digest="pending",
    )
    return bundle.model_copy(update={"digest": evidence_bundle_digest(bundle)})


def valid_hypothesis() -> TriageHypothesis:
    return TriageHypothesis(
        symptom_summary="The demo API item endpoint is returning a high 5xx ratio.",
        suspected_causes=[
            SuspectedCause(
                rank=1,
                title="Recent deployment",
                explanation="The latest deployment precedes the reproduced error spike.",
                evidence_ids=["ev-alert", "ev-ratio", "ev-deploy"],
                confidence=0.8,
            )
        ],
        contradictory_evidence_ids=[],
        missing_evidence=[],
        falsification_checks=["Compare the deployed commit with the preceding revision."],
        remediation_justified=True,
    )


def test_deterministic_validation_accepts_supported_hypothesis() -> None:
    assert validate_hypothesis(evidence_bundle(), valid_hypothesis()) == []


def test_deterministic_validation_rejects_missing_citation_and_unreproduced_symptom() -> None:
    bundle = evidence_bundle(error_ratio=0)
    hypothesis = valid_hypothesis().model_copy(deep=True)
    hypothesis.suspected_causes[0].evidence_ids = ["ev-missing"]

    errors = validate_hypothesis(bundle, hypothesis)

    assert any("unknown evidence" in error for error in errors)
    assert any("not reproduced" in error for error in errors)


def test_deterministic_validation_rejects_misleading_time_ordering() -> None:
    errors = validate_hypothesis(
        evidence_bundle(late_deployment=True),
        valid_hypothesis(),
    )

    assert any("occurs after the alert" in error for error in errors)


def test_review_cannot_approve_invalid_hypothesis() -> None:
    bundle = evidence_bundle(error_ratio=0)
    hypothesis_errors = validate_hypothesis(bundle, valid_hypothesis())
    review = TriageReview(
        verdict="approve",
        summary="Approve despite missing reproduction.",
        issues=[],
        evidence_ids=["ev-alert"],
    )

    errors = validate_review(bundle, hypothesis_errors, review)

    assert "review cannot approve an invalid hypothesis" in errors


def test_api_persists_only_matching_deterministic_validation(tmp_path: Path) -> None:
    active_settings = settings(tmp_path / "aiops.db")
    bundle = evidence_bundle()
    hypothesis = valid_hypothesis()
    review = TriageReview(
        verdict="approve",
        summary="The evidence supports the ranked cause.",
        issues=[],
        evidence_ids=["ev-alert", "ev-ratio", "ev-deploy"],
    )

    with TestClient(create_app(active_settings)) as client:
        incident_response: Response = client.post(
            "/api/v1/events/alertmanager",
            json=FIRING_PAYLOAD,
        )
        incident_id = incident_response.json()["incident_ids"][0]
        submission = TriageSubmission(
            incident_id=incident_id,
            evidence=bundle.model_copy(update={"incident_id": incident_id}),
            hypothesis=hypothesis,
            review=review,
            status="triaged",
            validation_errors=[],
            agent_runs=[],
        )
        submission.evidence = submission.evidence.model_copy(
            update={"digest": evidence_bundle_digest(submission.evidence)}
        )
        stored: Response = client.post(
            f"/api/v1/incidents/{incident_id}/triage",
            json=submission.model_dump(mode="json"),
        )
        latest: Response = client.get(f"/api/v1/incidents/{incident_id}/triage")
        invalid_payload = submission.model_dump(mode="json")
        invalid_payload["validation_errors"] = ["invented"]
        rejected: Response = client.post(
            f"/api/v1/incidents/{incident_id}/triage",
            json=invalid_payload,
        )

    assert stored.status_code == 201
    assert latest.json()["evidence"]["digest"] == submission.evidence.digest
    assert rejected.status_code == 422
