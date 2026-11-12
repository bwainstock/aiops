import hashlib
import subprocess
from pathlib import Path

from aiops_core.remediation_models import RemediationCandidate


class WorkspaceMerger:
    def __init__(self, *, repository: Path, worktrees_root: Path) -> None:
        self.repository = repository.resolve()
        self.worktrees_root = worktrees_root.resolve()

    def merge(self, candidate: RemediationCandidate) -> str:
        current_head = self._git_main("rev-parse", "HEAD").strip()
        if current_head != candidate.base_revision:
            raise ValueError("candidate base is stale")
        if self._git_main("status", "--porcelain=v1", "--untracked-files=all"):
            raise ValueError("main working tree is not clean")
        worktree = self.worktrees_root / candidate.id
        if not worktree.is_dir():
            raise ValueError("candidate worktree is missing")
        patch = self._git(worktree, "diff", "--no-ext-diff", "--binary", "--")
        if hashlib.sha256(patch.encode()).hexdigest() != candidate.patch_digest:
            raise ValueError("candidate patch digest changed")
        changed_files = [
            line for line in self._git(worktree, "diff", "--name-only", "--").splitlines() if line
        ]
        if sorted(changed_files) != sorted(candidate.changed_files):
            raise ValueError("candidate changed files changed")
        self._git(worktree, "add", "--", *changed_files)
        self._git(
            worktree,
            "commit",
            "-m",
            f"fix: approved remediation {candidate.id[:8]}",
            "-m",
            f"Incident: {candidate.incident_id}",
            "-m",
            "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>",
        )
        try:
            self._git_main(
                "merge",
                "--no-ff",
                candidate.branch,
                "-m",
                f"merge: approved remediation {candidate.id[:8]}",
            )
        except Exception:
            subprocess.run(
                ["git", "merge", "--abort"],
                cwd=self.repository,
                capture_output=True,
                check=False,
            )
            raise
        return self._git_main("rev-parse", "HEAD").strip()

    def _git_main(self, *arguments: str) -> str:
        return self._git(self.repository, *arguments)

    @staticmethod
    def _git(repository: Path, *arguments: str) -> str:
        completed = subprocess.run(
            ["git", "--no-pager", "-c", "color.ui=false", *arguments],
            cwd=repository,
            capture_output=True,
            check=False,
            timeout=60,
        )
        output = completed.stdout + completed.stderr
        if completed.returncode != 0:
            raise RuntimeError(output.decode(errors="replace"))
        return output.decode(errors="replace")
