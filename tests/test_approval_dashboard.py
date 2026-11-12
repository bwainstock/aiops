# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false

import hashlib
import subprocess
from pathlib import Path

from aiops_core.agent_models import AgentRunResult
from aiops_core.api import create_app
from aiops_core.config import Settings
from aiops_core.remediation_models import RemediationProposal, RemediationRequest
from aiops_core.remediation_workspace import RemediationWorkspace
from aiops_core.validation import validation_report_digest
from aiops_core.validation_models import (
    GateResult,
    ValidationReport,
    ValidationReview,
)
from fastapi.testclient import TestClient
from httpx2 import Response
from pytest import MonkeyPatch


def create_repository(tmp_path: Path) -> Path:
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "-b", "main")
    _git(repository, "config", "user.email", "test@example.com")
    _git(repository, "config", "user.name", "Test User")
    (repository / ".gitignore").write_text("worktrees/\n")
    (repository / "README.md").write_text("# Test\n")
    _git(repository, "add", ".gitignore", "README.md")
    _git(repository, "commit", "-m", "baseline")
    return repository


def candidate_and_report(repository: Path):
    request = RemediationRequest(
        incident_id="incident-approval",
        triage_status="triaged",
        base_revision=_git(repository, "rev-parse", "HEAD").strip(),
        hypothesis_digest=hashlib.sha256(b"hypothesis").hexdigest(),
        instructions="Create approval exercise file.",
        allowed_paths=["scenarios/**"],
    )
    workspace = RemediationWorkspace(
        repository=repository,
        worktrees_root=repository / "worktrees",
        request=request,
        candidate_id="candidate-approval",
    )
    workspace.create()
    workspace.write_file("scenarios/approved.txt", "approved\n")
    candidate = workspace.finalize(
        RemediationProposal(
            summary="Add approval exercise.",
            changed_files=["scenarios/approved.txt"],
            checks_run=[],
            residual_risks=[],
        )
    )
    review_run = AgentRunResult(
        run_id="run",
        session_id="session",
        profile="triage",
        provider="fake",
        output={},
        audit_events=[],
    )
    report = ValidationReport(
        candidate_id=candidate.id,
        incident_id=candidate.incident_id,
        base_revision=candidate.base_revision,
        hypothesis_digest=candidate.hypothesis_digest,
        patch_digest=candidate.patch_digest,
        gates=[
            GateResult(
                name="tests",
                status="passed",
                summary="tests passed",
            )
        ],
        review=ValidationReview(
            verdict="approve",
            summary="Candidate is valid.",
            issues=[],
        ),
        review_run=review_run,
        status="passed",
        digest="pending",
    )
    report = report.model_copy(update={"digest": validation_report_digest(report)})
    return candidate, report


def settings(database_path: Path) -> Settings:
    return Settings(
        database_path=database_path,
        worker_poll_seconds=0.01,
        job_lease_seconds=5,
        worker_processing_delay_seconds=0,
        job_max_attempts=3,
    )


def test_digest_bound_reject_and_approve_merge(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    repository = create_repository(tmp_path)
    candidate, report = candidate_and_report(repository)
    initial_head = _git(repository, "rev-parse", "HEAD").strip()
    monkeypatch.setenv("AIOPS_REPOSITORY_PATH", str(repository))

    with TestClient(create_app(settings(tmp_path / "aiops.db"))) as client:
        client.post("/api/v1/candidates", json=candidate.model_dump(mode="json"))
        client.post(
            f"/api/v1/candidates/{candidate.id}/validation",
            json=report.model_dump(mode="json"),
        )
        republished: Response = client.post(
            "/api/v1/candidates",
            json=candidate.model_dump(mode="json"),
        )
        stale: Response = client.post(
            f"/api/v1/candidates/{candidate.id}/decision",
            json={
                "decision": "approve",
                "candidate_digest": "0" * 64,
                "validation_digest": report.digest,
                "reason": "Wrong digest should fail.",
            },
        )
        rejected: Response = client.post(
            f"/api/v1/candidates/{candidate.id}/decision",
            json={
                "decision": "reject",
                "candidate_digest": candidate.patch_digest,
                "validation_digest": report.digest,
                "reason": "Exercise rejection.",
            },
        )
        head_after_reject = _git(repository, "rev-parse", "HEAD").strip()
        dashboard: Response = client.get("/dashboard")
        approved: Response = client.post(
            f"/api/v1/candidates/{candidate.id}/decision",
            json={
                "decision": "approve",
                "candidate_digest": candidate.patch_digest,
                "validation_digest": report.digest,
                "reason": "Exercise approval.",
            },
        )

    assert stale.status_code == 409
    assert republished.status_code == 201
    assert rejected.status_code == 200
    assert head_after_reject == initial_head
    assert "Candidate candidat" in dashboard.text
    assert approved.status_code == 200
    assert approved.json()["merge_commit"] == _git(repository, "rev-parse", "HEAD").strip()
    assert (repository / "scenarios/approved.txt").read_text() == "approved\n"
    assert _git(repository, "status", "--porcelain").strip() == ""


def test_stale_base_is_rejected(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    repository = create_repository(tmp_path)
    candidate, report = candidate_and_report(repository)
    (repository / "later.txt").write_text("later\n")
    _git(repository, "add", "later.txt")
    _git(repository, "commit", "-m", "later")
    monkeypatch.setenv("AIOPS_REPOSITORY_PATH", str(repository))

    with TestClient(create_app(settings(tmp_path / "aiops.db"))) as client:
        client.post("/api/v1/candidates", json=candidate.model_dump(mode="json"))
        client.post(
            f"/api/v1/candidates/{candidate.id}/validation",
            json=report.model_dump(mode="json"),
        )
        response: Response = client.post(
            f"/api/v1/candidates/{candidate.id}/decision",
            json={
                "decision": "approve",
                "candidate_digest": candidate.patch_digest,
                "validation_digest": report.digest,
                "reason": "Stale base should fail.",
            },
        )

    assert response.status_code == 409
    assert response.json()["detail"] == "candidate base is stale"


def _git(repository: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=repository,
        capture_output=True,
        check=True,
        text=True,
    )
    return completed.stdout
