import hashlib
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from aiops_core.agent_models import AgentRunResult
from aiops_core.candidate_repository import CandidateRepository
from aiops_core.database import Database
from aiops_core.intake import IncidentIntake, isoformat
from aiops_core.remediation_models import RemediationProposal, RemediationRequest
from aiops_core.remediation_workspace import RemediationWorkspace
from aiops_core.triage_models import (
    EvidenceBundle,
    EvidenceItem,
    SuspectedCause,
    TriageHypothesis,
    TriageReview,
    TriageSubmission,
)
from aiops_core.triage_repository import TriageRepository
from aiops_core.triage_validation import (
    evidence_bundle_digest,
    evidence_item_digest,
    validate_hypothesis,
    validate_review,
)
from aiops_core.validation import determine_validation_status, validation_report_digest
from aiops_core.validation_models import GateResult, ValidationReport, ValidationReview
from aiops_core.workflow import WorkflowQueue
from aiops_core.workspace_merge import WorkspaceMerger
from aiops_shared.alertmanager import AlertmanagerWebhook


@pytest.mark.parametrize(
    ("scenario", "alertname", "symptom"),
    [
        ("error", "HighErrorRate", "errors"),
        ("latency", "HighLatencyP95", "latency"),
    ],
)
def test_fake_agent_full_incident_to_merge_scenario(
    tmp_path: Path,
    scenario: str,
    alertname: str,
    symptom: str,
) -> None:
    repository = _repository(tmp_path)
    database = Database(tmp_path / "aiops.db")
    now = datetime.now(UTC)
    database.initialize(applied_at=isoformat(now))
    intake = IncidentIntake(database, job_max_attempts=3)
    webhook = AlertmanagerWebhook.model_validate(
        _alert_payload(
            alertname=alertname,
            symptom=symptom,
            fingerprint=f"fingerprint-{scenario}",
            now=now,
        )
    )
    ingest = intake.ingest_alertmanager(webhook)
    queue = WorkflowQueue(database, lease_seconds=5)
    job = queue.claim_next(worker_id="e2e-worker", now=now + timedelta(seconds=1))
    assert job is not None
    queue.complete(job, now=now + timedelta(seconds=2))

    evidence = _evidence_bundle(
        incident_id=ingest.incident_ids[0],
        alertname=alertname,
        now=now,
    )
    hypothesis = TriageHypothesis(
        symptom_summary=f"{alertname} reproduced in deterministic scenario.",
        suspected_causes=[
            SuspectedCause(
                rank=1,
                title=f"Controlled {scenario} regression",
                explanation="The scenario evidence directly reproduces the symptom.",
                evidence_ids=["ev-alert", "ev-reproduction"],
                confidence=1,
            )
        ],
        contradictory_evidence_ids=[],
        missing_evidence=[],
        falsification_checks=["Disable the controlled scenario and repeat."],
        remediation_justified=True,
    )
    review = TriageReview(
        verdict="approve",
        summary="Frozen evidence supports the controlled scenario.",
        issues=[],
        evidence_ids=["ev-alert", "ev-reproduction"],
    )
    assert validate_hypothesis(evidence, hypothesis) == []
    assert validate_review(evidence, [], review) == []
    fake_run = _agent_run(f"triage-{scenario}")
    TriageRepository(database).store(
        TriageSubmission(
            incident_id=ingest.incident_ids[0],
            evidence=evidence,
            hypothesis=hypothesis,
            review=review,
            status="triaged",
            validation_errors=[],
            agent_runs=[fake_run],
        )
    )

    request = RemediationRequest(
        incident_id=ingest.incident_ids[0],
        triage_status="triaged",
        base_revision=_git(repository, "rev-parse", "HEAD").strip(),
        hypothesis_digest=hashlib.sha256(hypothesis.model_dump_json().encode()).hexdigest(),
        instructions=f"Create the {scenario} scenario result.",
        allowed_paths=["scenarios/**"],
    )
    workspace = RemediationWorkspace(
        repository=repository,
        worktrees_root=repository / "worktrees",
        request=request,
        candidate_id=f"candidate-{scenario}",
    )
    workspace.create()
    result_path = f"scenarios/{scenario}-resolved.txt"
    workspace.write_file(result_path, "resolved\n")
    candidate = workspace.finalize(
        RemediationProposal(
            summary=f"Resolve the controlled {scenario} scenario.",
            changed_files=[result_path],
            checks_run=["tests"],
            residual_risks=[],
        )
    )
    gates = [
        GateResult(name="pre_fix_reproduction", status="passed", summary="failed before"),
        GateResult(name="post_fix_scenario", status="passed", summary="passed after"),
        GateResult(name="tests", status="passed", summary="tests passed"),
    ]
    validation_review = ValidationReview(
        verdict="approve",
        summary="All deterministic gates passed.",
        issues=[],
    )
    assert determine_validation_status(gates, validation_review) == "passed"
    report = ValidationReport(
        candidate_id=candidate.id,
        incident_id=candidate.incident_id,
        base_revision=candidate.base_revision,
        hypothesis_digest=candidate.hypothesis_digest,
        patch_digest=candidate.patch_digest,
        gates=gates,
        review=validation_review,
        review_run=_agent_run(f"review-{scenario}"),
        status="passed",
        digest="pending",
    )
    report = report.model_copy(update={"digest": validation_report_digest(report)})
    candidates = CandidateRepository(database)
    candidates.store_candidate(candidate)
    candidates.store_validation(report)
    merge_commit = WorkspaceMerger(
        repository=repository,
        worktrees_root=repository / "worktrees",
    ).merge(candidate)
    decision = candidates.record_decision(
        candidate_id=candidate.id,
        candidate_digest=candidate.patch_digest,
        validation_digest=report.digest,
        decision="approve",
        reason="E2E scenario approval.",
        merge_commit=merge_commit,
    )

    assert (repository / result_path).read_text() == "resolved\n"
    assert decision.merge_commit == _git(repository, "rev-parse", "HEAD").strip()
    assert candidates.latest_decision(candidate.id) is not None


def _repository(tmp_path: Path) -> Path:
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "-b", "main")
    _git(repository, "config", "user.email", "test@example.com")
    _git(repository, "config", "user.name", "Test User")
    (repository / ".gitignore").write_text("worktrees/\n")
    (repository / "README.md").write_text("# E2E\n")
    _git(repository, "add", ".gitignore", "README.md")
    _git(repository, "commit", "-m", "baseline")
    return repository


def _alert_payload(
    *,
    alertname: str,
    symptom: str,
    fingerprint: str,
    now: datetime,
) -> dict[str, object]:
    timestamp = now.isoformat().replace("+00:00", "Z")
    labels = {
        "alertname": alertname,
        "service": "demo-api",
        "severity": "warning",
        "symptom": symptom,
    }
    return {
        "version": "4",
        "groupKey": f'{{}}:{{alertname="{alertname}"}}',
        "truncatedAlerts": 0,
        "status": "firing",
        "receiver": "aiops",
        "groupLabels": {"alertname": alertname},
        "commonLabels": labels,
        "commonAnnotations": {"summary": f"{alertname} fired"},
        "externalURL": "http://alertmanager:9093",
        "alerts": [
            {
                "status": "firing",
                "labels": labels,
                "annotations": {"summary": f"{alertname} fired"},
                "startsAt": timestamp,
                "endsAt": "0001-01-01T00:00:00Z",
                "generatorURL": "http://prometheus:9090/graph",
                "fingerprint": fingerprint,
            }
        ],
    }


def _evidence_bundle(
    *,
    incident_id: str,
    alertname: str,
    now: datetime,
) -> EvidenceBundle:
    alert = EvidenceItem(
        id="ev-alert",
        source="alertmanager",
        kind="alert.firing",
        observed_at=now,
        content={"alertname": alertname},
        digest="pending",
    )
    alert = alert.model_copy(update={"digest": evidence_item_digest(alert)})
    reproduction = EvidenceItem(
        id="ev-reproduction",
        source="prometheus",
        kind="symptom_reproduction",
        observed_at=now + timedelta(seconds=1),
        content={"reproduced": True},
        digest="pending",
    )
    reproduction = reproduction.model_copy(update={"digest": evidence_item_digest(reproduction)})
    bundle = EvidenceBundle(
        id=f"bundle-{alertname}",
        incident_id=incident_id,
        collected_at=now + timedelta(seconds=2),
        window_start=now - timedelta(minutes=1),
        window_end=now + timedelta(minutes=1),
        items=[alert, reproduction],
        digest="pending",
    )
    return bundle.model_copy(update={"digest": evidence_bundle_digest(bundle)})


def _agent_run(run_id: str) -> AgentRunResult:
    return AgentRunResult(
        run_id=run_id,
        session_id=f"session-{run_id}",
        profile="triage",
        provider="fake",
        output={},
        audit_events=[],
    )


def _git(repository: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=repository,
        capture_output=True,
        check=True,
        text=True,
    )
    return completed.stdout
