from pathlib import Path
from typing import Literal, cast

from aiops_core.agent_models import AgentRunResult
from aiops_core.agent_runner import AgentRunner
from aiops_core.remediation_models import RemediationCandidate, RemediationProposal
from aiops_core.validation import (
    CandidateValidator,
    determine_validation_status,
    validation_report_digest,
)
from aiops_core.validation_models import (
    GateResult,
    ValidationReport,
    ValidationReview,
)


def review(
    verdict: Literal["approve", "reject"] = "approve",
) -> ValidationReview:
    return ValidationReview(
        verdict=verdict,
        summary="Independent review completed.",
        issues=[] if verdict == "approve" else ["A gate failed."],
    )


def gate(
    name: str,
    status: Literal["passed", "failed"] = "passed",
) -> GateResult:
    return GateResult(
        name=name,
        status=status,
        summary=f"{name} {status}",
    )


def test_deterministic_failure_cannot_be_overridden_by_review() -> None:
    assert (
        determine_validation_status(
            [gate("tests", "failed")],
            review("approve"),
        )
        == "failed"
    )


def test_review_rejection_fails_an_all_green_candidate() -> None:
    assert (
        determine_validation_status(
            [gate("tests"), gate("integration")],
            review("reject"),
        )
        == "failed"
    )


def test_all_gates_and_review_are_required_to_pass() -> None:
    assert (
        determine_validation_status(
            [gate("tests"), gate("integration")],
            review("approve"),
        )
        == "passed"
    )


def test_validation_report_digest_changes_with_gate_result() -> None:
    run = AgentRunResult(
        run_id="run-1",
        session_id="session-1",
        profile="triage",
        provider="fake",
        output={},
        audit_events=[],
    )
    report = ValidationReport(
        candidate_id="candidate",
        incident_id="incident",
        base_revision="a" * 40,
        hypothesis_digest="b" * 64,
        patch_digest="c" * 64,
        gates=[gate("tests")],
        review=review(),
        review_run=run,
        status="passed",
        digest="pending",
    )
    first = validation_report_digest(report)
    modified = report.model_copy(update={"gates": [gate("tests", "failed")]})

    assert first != validation_report_digest(modified)


def test_invalid_candidate_fails_patch_policy_gate(tmp_path: Path) -> None:
    candidate = RemediationCandidate(
        id="candidate",
        incident_id="incident",
        base_revision="a" * 40,
        branch="aiops/incident/candidate",
        worktree_path=tmp_path,
        hypothesis_digest="b" * 64,
        patch_digest="c" * 64,
        changed_files=["missing.txt"],
        patch="invalid",
        status="candidate",
        proposal=RemediationProposal(
            summary="Invalid candidate.",
            changed_files=["missing.txt"],
            checks_run=[],
            residual_risks=[],
        ),
    )
    validator = CandidateValidator(
        repository=tmp_path,
        reviewer=cast(AgentRunner, object()),
        worktrees_root=tmp_path / "worktrees",
    )

    result = validator.validate_patch(candidate)

    assert result.name == "patch_policy"
    assert result.status == "failed"
