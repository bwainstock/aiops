import hashlib
import subprocess
from pathlib import Path
from typing import cast

import pytest
from aiops_core.remediation_models import RemediationProposal, RemediationRequest
from aiops_core.remediation_workspace import CheckName, RemediationWorkspace


def create_repository(tmp_path: Path) -> Path:
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "-b", "main")
    _git(repository, "config", "user.email", "test@example.com")
    _git(repository, "config", "user.name", "Test User")
    (repository / "README.md").write_text("# Test\n")
    _git(repository, "add", "README.md")
    _git(repository, "commit", "-m", "baseline")
    return repository


def request(repository: Path) -> RemediationRequest:
    return RemediationRequest(
        incident_id="incident-123",
        triage_status="triaged",
        base_revision=_git(repository, "rev-parse", "HEAD").strip(),
        hypothesis_digest=hashlib.sha256(b"hypothesis").hexdigest(),
        instructions="Create a bounded remediation candidate.",
        allowed_paths=["scenarios/**", "tests/**"],
    )


def test_worktree_candidate_isolated_and_digest_bound(tmp_path: Path) -> None:
    repository = create_repository(tmp_path)
    workspace = RemediationWorkspace(
        repository=repository,
        worktrees_root=tmp_path / "worktrees",
        request=request(repository),
        candidate_id="candidate-1234",
    )
    before = workspace.main_snapshot()
    workspace.create()
    workspace.write_file(
        "scenarios/remediation.txt",
        "status=healthy\n",
    )
    candidate = workspace.finalize(
        RemediationProposal(
            summary="Add the isolated remediation marker.",
            changed_files=["scenarios/remediation.txt"],
            checks_run=[],
            residual_risks=[],
        )
    )

    assert workspace.main_snapshot() == before
    assert not (repository / "scenarios/remediation.txt").exists()
    assert (workspace.path / "scenarios/remediation.txt").read_text() == "status=healthy\n"
    assert candidate.base_revision == before.head
    assert candidate.hypothesis_digest == request(repository).hypothesis_digest
    assert candidate.patch_digest == hashlib.sha256(candidate.patch.encode()).hexdigest()
    assert candidate.changed_files == ["scenarios/remediation.txt"]


def test_workspace_denies_paths_secrets_and_unapproved_checks(
    tmp_path: Path,
) -> None:
    repository = create_repository(tmp_path)
    workspace = RemediationWorkspace(
        repository=repository,
        worktrees_root=tmp_path / "worktrees",
        request=request(repository),
    )
    workspace.create()

    with pytest.raises(ValueError, match="outside"):
        workspace.write_file("README.md", "changed\n")
    with pytest.raises(ValueError, match="escapes"):
        workspace.write_file("../outside.txt", "changed\n")
    with pytest.raises(ValueError, match="sensitive"):
        workspace.write_file("scenarios/.env", "TOKEN=secret\n")
    with pytest.raises(ValueError, match="allowlisted"):
        workspace.run_check(cast(CheckName, "git_push"))


def test_remediation_rejects_non_triaged_conclusion(tmp_path: Path) -> None:
    repository = create_repository(tmp_path)
    blocked = request(repository).model_copy(update={"triage_status": "more_evidence"})

    with pytest.raises(ValueError, match="requires a triaged conclusion"):
        RemediationWorkspace(
            repository=repository,
            worktrees_root=tmp_path / "worktrees",
            request=blocked,
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
