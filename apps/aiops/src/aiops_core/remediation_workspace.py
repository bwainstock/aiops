import fnmatch
import hashlib
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from uuid import uuid4

from aiops_shared.security import redact_text, resolve_repository_path, validate_revision

from aiops_core.remediation_models import (
    RemediationCandidate,
    RemediationProposal,
    RemediationRequest,
)

CheckName = Literal["format_check", "lint", "typecheck", "test", "compose_config"]

CHECK_COMMANDS: dict[CheckName, tuple[str, ...]] = {
    "format_check": ("uv", "run", "--all-packages", "ruff", "format", "--check", "."),
    "lint": ("uv", "run", "--all-packages", "ruff", "check", "."),
    "typecheck": ("uv", "run", "--all-packages", "pyright"),
    "test": ("uv", "run", "--all-packages", "pytest"),
    "compose_config": ("docker", "compose", "config", "--quiet"),
}
SENSITIVE_NAMES = {
    ".env",
    ".env.local",
    "credentials",
    "credentials.json",
    "id_rsa",
    "id_ed25519",
    "secrets.yml",
    "secrets.yaml",
}


@dataclass(frozen=True)
class WorkspaceSnapshot:
    status: str
    head: str


class RemediationWorkspace:
    def __init__(
        self,
        *,
        repository: Path,
        worktrees_root: Path,
        request: RemediationRequest,
        candidate_id: str | None = None,
        max_patch_bytes: int = 262_144,
    ) -> None:
        if request.triage_status != "triaged":
            raise ValueError("remediation requires a triaged conclusion")
        self.repository = repository.resolve()
        self.worktrees_root = worktrees_root.resolve()
        self.request = request
        self.candidate_id = candidate_id or str(uuid4())
        self.max_patch_bytes = max_patch_bytes
        self.branch = f"aiops/{_slug(request.incident_id)}/{self.candidate_id[:8]}"
        self.path = self.worktrees_root / self.candidate_id
        self._main_snapshot: WorkspaceSnapshot | None = None

    def create(self) -> Path:
        if self.path.exists():
            raise ValueError("candidate worktree already exists")
        base_revision = validate_revision(self.request.base_revision)
        resolved_base = self._git_main("rev-parse", "--verify", f"{base_revision}^{{commit}}")
        self._main_snapshot = self.main_snapshot()
        self.worktrees_root.mkdir(parents=True, exist_ok=True)
        self._git_main(
            "worktree",
            "add",
            "-b",
            self.branch,
            str(self.path),
            resolved_base.strip(),
        )
        return self.path

    def read_file(self, relative_path: str, *, max_bytes: int = 65_536) -> str:
        path = self._allowed_path(relative_path)
        if not path.is_file():
            raise ValueError("file does not exist")
        payload = path.read_bytes()
        if len(payload) > max_bytes:
            raise ValueError("file exceeds the read limit")
        return redact_text(payload.decode(errors="replace"), max_length=max_bytes)

    def write_file(
        self,
        relative_path: str,
        content: str,
        *,
        max_bytes: int = 131_072,
    ) -> None:
        path = self._allowed_path(relative_path)
        encoded = content.encode()
        if len(encoded) > max_bytes:
            raise ValueError("file exceeds the write limit")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(encoded)
        self._git_worktree("add", "-N", "--", relative_path)

    def run_check(self, check: CheckName) -> dict[str, object]:
        command = CHECK_COMMANDS.get(check)
        if command is None:
            raise ValueError("check is not allowlisted")
        completed = subprocess.run(
            command,
            cwd=self.path,
            capture_output=True,
            check=False,
            timeout=300,
        )
        output = redact_text(
            (completed.stdout + completed.stderr).decode(errors="replace"),
            max_length=65_536,
        )
        return {
            "check": check,
            "success": completed.returncode == 0,
            "exit_code": completed.returncode,
            "output": output,
        }

    def patch(self) -> tuple[str, list[str], str]:
        self._git_worktree("diff", "--check")
        changed_files = [
            line
            for line in self._git_worktree(
                "diff",
                "--name-only",
                "--",
            ).splitlines()
            if line
        ]
        if not changed_files:
            raise ValueError("candidate contains no changes")
        for changed_file in changed_files:
            self._allowed_path(changed_file)
        patch = self._git_worktree(
            "diff",
            "--no-ext-diff",
            "--binary",
            "--",
        )
        encoded = patch.encode()
        if len(encoded) > self.max_patch_bytes:
            raise ValueError("candidate patch exceeds the size limit")
        lowered = patch.lower()
        if "begin private key" in lowered or "github_pat_" in lowered:
            raise ValueError("candidate patch appears to contain a secret")
        digest = hashlib.sha256(encoded).hexdigest()
        return patch, changed_files, digest

    def finalize(self, proposal: RemediationProposal) -> RemediationCandidate:
        patch, changed_files, patch_digest = self.patch()
        if sorted(proposal.changed_files) != sorted(changed_files):
            raise ValueError("proposal changed files do not match the candidate patch")
        self.assert_main_unchanged()
        return RemediationCandidate(
            id=self.candidate_id,
            incident_id=self.request.incident_id,
            base_revision=self._git_worktree("rev-parse", "HEAD").strip(),
            branch=self.branch,
            worktree_path=self.path,
            hypothesis_digest=self.request.hypothesis_digest,
            patch_digest=patch_digest,
            changed_files=changed_files,
            patch=patch,
            status="candidate",
            proposal=proposal,
        )

    def assert_main_unchanged(self) -> None:
        if self._main_snapshot is None:
            raise RuntimeError("worktree has not been created")
        if self.main_snapshot() != self._main_snapshot:
            raise RuntimeError("main working tree changed during remediation")

    def main_snapshot(self) -> WorkspaceSnapshot:
        return WorkspaceSnapshot(
            status=self._git_main("status", "--porcelain=v1", "--untracked-files=all"),
            head=self._git_main("rev-parse", "HEAD").strip(),
        )

    def _allowed_path(self, relative_path: str) -> Path:
        path = resolve_repository_path(self.path, relative_path)
        normalized = str(path.relative_to(self.path))
        if path.name.lower() in SENSITIVE_NAMES or any(
            part.lower() in {".git", ".ssh"} for part in path.parts
        ):
            raise ValueError("sensitive path is not writable")
        if not any(fnmatch.fnmatch(normalized, pattern) for pattern in self.request.allowed_paths):
            raise ValueError("path is outside the remediation allowlist")
        return path

    def _git_main(self, *arguments: str) -> str:
        return _run_git(self.repository, *arguments)

    def _git_worktree(self, *arguments: str) -> str:
        return _run_git(self.path, *arguments)


def _run_git(repository: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "--no-pager", "-c", "color.ui=false", *arguments],
        cwd=repository,
        capture_output=True,
        check=False,
        timeout=30,
    )
    output = completed.stdout + completed.stderr
    text = output.decode(errors="replace")
    if completed.returncode != 0:
        raise RuntimeError(f"Git command failed with exit code {completed.returncode}: {text}")
    return text


def _slug(value: str) -> str:
    normalized = "".join(
        character.lower() if character.isalnum() else "-" for character in value
    ).strip("-")
    return normalized[:40] or "incident"
