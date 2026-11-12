import subprocess
from dataclasses import dataclass
from pathlib import Path

from aiops_ops_mcp.security import (
    redact_text,
    resolve_repository_path,
    validate_revision,
)


@dataclass(frozen=True)
class GitAdapter:
    repository: Path
    timeout_seconds: float = 10
    max_output_bytes: int = 131_072

    def status(self) -> dict[str, object]:
        return self._result("status", "--short", "--branch")

    def log(
        self,
        *,
        revision: str = "HEAD",
        limit: int = 20,
        path: str | None = None,
    ) -> dict[str, object]:
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        arguments = [
            "log",
            validate_revision(revision),
            "-n",
            str(limit),
            "--date=iso-strict",
            "--pretty=format:%H%x09%aI%x09%an%x09%s",
        ]
        if path is not None:
            arguments.extend(["--", self._relative_path(path)])
        return self._result(*arguments)

    def show(self, revision: str = "HEAD", *, path: str | None = None) -> dict[str, object]:
        arguments = [
            "show",
            "--no-ext-diff",
            "--format=fuller",
            "--stat",
            "--patch",
            validate_revision(revision),
        ]
        if path is not None:
            arguments.extend(["--", self._relative_path(path)])
        return self._result(*arguments)

    def diff(
        self,
        *,
        base: str,
        head: str = "HEAD",
        path: str | None = None,
    ) -> dict[str, object]:
        arguments = [
            "diff",
            "--no-ext-diff",
            f"{validate_revision(base)}...{validate_revision(head)}",
        ]
        if path is not None:
            arguments.extend(["--", self._relative_path(path)])
        return self._result(*arguments)

    def blame(
        self,
        *,
        path: str,
        start_line: int = 1,
        end_line: int = 200,
    ) -> dict[str, object]:
        if start_line < 1 or end_line < start_line or end_line - start_line > 500:
            raise ValueError("invalid blame line range")
        return self._result(
            "blame",
            "--line-porcelain",
            "-L",
            f"{start_line},{end_line}",
            "--",
            self._relative_path(path),
        )

    def _relative_path(self, value: str) -> str:
        resolved = resolve_repository_path(self.repository, value)
        return str(resolved.relative_to(self.repository))

    def _result(self, *arguments: str) -> dict[str, object]:
        completed = subprocess.run(
            [
                "git",
                "--no-pager",
                "-c",
                "color.ui=false",
                "-c",
                f"safe.directory={self.repository}",
                *arguments,
            ],
            cwd=self.repository,
            capture_output=True,
            check=False,
            timeout=self.timeout_seconds,
        )
        output = completed.stdout + completed.stderr
        truncated = len(output) > self.max_output_bytes
        bounded = output[: self.max_output_bytes]
        text = redact_text(bounded.decode(errors="replace"))
        if completed.returncode != 0:
            raise RuntimeError(f"Git command failed with exit code {completed.returncode}: {text}")
        return {
            "command": ["git", *arguments],
            "output": text,
            "truncated": truncated,
        }
