import hashlib
import json
import os
import socket
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol
from urllib.request import urlopen
from uuid import uuid4

from aiops_ops_mcp.security import redact_text

from aiops_core.agent_models import AgentTask
from aiops_core.agent_runner import AgentRunner
from aiops_core.remediation_models import RemediationCandidate
from aiops_core.validation_models import (
    GateResult,
    ValidationReport,
    ValidationReview,
)


class Scenario(Protocol):
    def evaluate(self, repository: Path) -> tuple[bool, str]: ...


@dataclass(frozen=True)
class FileContentScenario:
    relative_path: str
    expected_content: str

    def evaluate(self, repository: Path) -> tuple[bool, str]:
        path = repository / self.relative_path
        if not path.is_file():
            return False, f"{self.relative_path} does not exist"
        actual = path.read_text()
        if actual != self.expected_content:
            return False, f"{self.relative_path} content did not match"
        return True, f"{self.relative_path} matched expected content"


class CandidateValidator:
    def __init__(
        self,
        *,
        repository: Path,
        reviewer: AgentRunner,
        worktrees_root: Path,
    ) -> None:
        self.repository = repository.resolve()
        self.reviewer = reviewer
        self.worktrees_root = worktrees_root.resolve()

    async def validate(
        self,
        candidate: RemediationCandidate,
        scenario: Scenario,
    ) -> ValidationReport:
        gates: list[GateResult] = []
        gates.append(self.validate_patch(candidate))
        gates.extend(self._scenario_gates(candidate, scenario))
        for name, command in (
            ("format", ("uv", "run", "--all-packages", "ruff", "format", "--check", ".")),
            ("lint", ("uv", "run", "--all-packages", "ruff", "check", ".")),
            ("typecheck", ("uv", "run", "--all-packages", "pyright")),
            ("tests", ("uv", "run", "--all-packages", "pytest")),
            ("compose_config", ("docker", "compose", "config", "--quiet")),
        ):
            gates.append(self._command_gate(name, candidate.worktree_path, command))
        gates.extend(self._integration_gates(candidate))

        review_run, review = await self.reviewer.run(
            AgentTask(
                profile="triage",
                prompt=_review_prompt(candidate, gates),
                timeout_seconds=240,
            ),
            ValidationReview,
        )
        status = determine_validation_status(gates, review)
        report = ValidationReport(
            candidate_id=candidate.id,
            incident_id=candidate.incident_id,
            base_revision=candidate.base_revision,
            hypothesis_digest=candidate.hypothesis_digest,
            patch_digest=candidate.patch_digest,
            gates=gates,
            review=review,
            review_run=review_run,
            status=status,
            digest="pending",
        )
        return report.model_copy(update={"digest": validation_report_digest(report)})

    def validate_patch(self, candidate: RemediationCandidate) -> GateResult:
        try:
            patch = _git(candidate.worktree_path, "diff", "--no-ext-diff", "--binary", "--")
            changed_files = [
                line
                for line in _git(
                    candidate.worktree_path,
                    "diff",
                    "--name-only",
                    "--",
                ).splitlines()
                if line
            ]
            digest = hashlib.sha256(patch.encode()).hexdigest()
            if digest != candidate.patch_digest:
                raise ValueError("patch digest does not match candidate")
            if sorted(changed_files) != sorted(candidate.changed_files):
                raise ValueError("changed files do not match candidate")
            if patch != candidate.patch:
                raise ValueError("patch content does not match candidate")
            _git(candidate.worktree_path, "diff", "--check")
            return GateResult(
                name="patch_policy",
                status="passed",
                summary="Candidate patch and changed files match their digests.",
            )
        except Exception as error:
            return GateResult(
                name="patch_policy",
                status="failed",
                summary=str(error),
            )

    def _scenario_gates(
        self,
        candidate: RemediationCandidate,
        scenario: Scenario,
    ) -> list[GateResult]:
        validation_id = uuid4().hex[:12]
        base_path = self.worktrees_root / f"validation-base-{validation_id}"
        self.worktrees_root.mkdir(parents=True, exist_ok=True)
        try:
            _git(
                self.repository,
                "worktree",
                "add",
                "--detach",
                str(base_path),
                candidate.base_revision,
            )
            base_passed, base_summary = scenario.evaluate(base_path)
            candidate_passed, candidate_summary = scenario.evaluate(candidate.worktree_path)
            return [
                GateResult(
                    name="pre_fix_reproduction",
                    status="passed" if not base_passed else "failed",
                    summary=(
                        f"Base reproduced failure: {base_summary}"
                        if not base_passed
                        else "Base unexpectedly passed the scenario."
                    ),
                ),
                GateResult(
                    name="post_fix_scenario",
                    status="passed" if candidate_passed else "failed",
                    summary=candidate_summary,
                ),
            ]
        finally:
            if base_path.exists():
                _git(
                    self.repository,
                    "worktree",
                    "remove",
                    "--force",
                    str(base_path),
                )

    def _command_gate(
        self,
        name: str,
        working_directory: Path,
        command: tuple[str, ...],
    ) -> GateResult:
        completed = subprocess.run(
            command,
            cwd=working_directory,
            capture_output=True,
            check=False,
            timeout=600,
        )
        output = redact_text(
            (completed.stdout + completed.stderr).decode(errors="replace"),
            max_length=32_768,
        )
        return GateResult(
            name=name,
            status="passed" if completed.returncode == 0 else "failed",
            summary=(
                f"{name} passed"
                if completed.returncode == 0
                else f"{name} failed with exit code {completed.returncode}"
            ),
            output=output,
        )

    def _integration_gates(
        self,
        candidate: RemediationCandidate,
    ) -> list[GateResult]:
        project = f"aiops-validation-{candidate.id[:8]}"
        port = _free_port()
        environment = os.environ | {
            "COMPOSE_PROJECT_NAME": project,
            "DEMO_API_PORT": str(port),
        }
        compose = (
            "docker",
            "compose",
            "-p",
            project,
        )
        gates: list[GateResult] = []
        try:
            start = subprocess.run(
                (*compose, "up", "-d", "--build", "demo-api"),
                cwd=candidate.worktree_path,
                env=environment,
                capture_output=True,
                check=False,
                timeout=900,
            )
            start_output = redact_text(
                (start.stdout + start.stderr).decode(errors="replace"),
                max_length=32_768,
            )
            gates.append(
                GateResult(
                    name="container_build_start",
                    status="passed" if start.returncode == 0 else "failed",
                    summary=(
                        "Candidate container built and started."
                        if start.returncode == 0
                        else "Candidate container failed to build or start."
                    ),
                    output=start_output,
                )
            )
            if start.returncode != 0:
                return gates
            healthy = _wait_for_url(f"http://127.0.0.1:{port}/healthz")
            gates.append(
                GateResult(
                    name="healthcheck",
                    status="passed" if healthy else "failed",
                    summary=(
                        "Candidate health endpoint became ready."
                        if healthy
                        else "Candidate health endpoint did not become ready."
                    ),
                )
            )
            if not healthy:
                return gates
            load = subprocess.run(
                (
                    *compose,
                    "--profile",
                    "load",
                    "run",
                    "--rm",
                    "loadgen",
                    "aiops-loadgen",
                    "--base-url",
                    "http://demo-api:8000",
                    "--requests",
                    "100",
                ),
                cwd=candidate.worktree_path,
                env=environment,
                capture_output=True,
                check=False,
                timeout=300,
            )
            load_output = redact_text(
                (load.stdout + load.stderr).decode(errors="replace"),
                max_length=16_384,
            )
            gates.append(
                GateResult(
                    name="healthy_load",
                    status="passed" if load.returncode == 0 else "failed",
                    summary=(
                        "Candidate served 100 healthy load requests."
                        if load.returncode == 0
                        else "Candidate failed healthy load."
                    ),
                    output=load_output,
                )
            )
            metrics = _read_url(f"http://127.0.0.1:{port}/metrics")
            no_5xx = (
                'demo_http_requests_total{method="GET",route="/api/items",status_code="500"}'
                not in metrics
            )
            logs = subprocess.run(
                (*compose, "logs", "--no-log-prefix", "demo-api"),
                cwd=candidate.worktree_path,
                env=environment,
                capture_output=True,
                check=False,
                timeout=30,
            ).stdout.decode(errors="replace")
            no_error_logs = '"level":"error"' not in logs
            gates.append(
                GateResult(
                    name="telemetry_recovery",
                    status="passed" if no_5xx and no_error_logs else "failed",
                    summary=(
                        "Candidate produced no 5xx metrics or error-level request logs."
                        if no_5xx and no_error_logs
                        else "Candidate telemetry contained 5xx or error-level request logs."
                    ),
                )
            )
            return gates
        finally:
            subprocess.run(
                (*compose, "down", "--volumes", "--remove-orphans"),
                cwd=candidate.worktree_path,
                env=environment,
                capture_output=True,
                check=False,
                timeout=180,
            )


def validation_report_digest(report: ValidationReport) -> str:
    encoded = json.dumps(
        report.model_dump(mode="json", exclude={"digest"}),
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def determine_validation_status(
    gates: list[GateResult],
    review: ValidationReview,
) -> Literal["passed", "failed"]:
    if not all(gate.status == "passed" for gate in gates):
        return "failed"
    return "passed" if review.verdict == "approve" else "failed"


def _review_prompt(
    candidate: RemediationCandidate,
    gates: list[GateResult],
) -> str:
    return (
        "Independently review this frozen candidate patch and deterministic gate "
        "matrix. Approve only if every gate passed and the patch is consistent with "
        "the stated isolated remediation exercise. A deterministic failure can never "
        "be overridden. Return reject with explicit issues otherwise.\n\n"
        + json.dumps(
            {
                "candidate": {
                    "id": candidate.id,
                    "base_revision": candidate.base_revision,
                    "hypothesis_digest": candidate.hypothesis_digest,
                    "patch_digest": candidate.patch_digest,
                    "changed_files": candidate.changed_files,
                    "patch": candidate.patch,
                },
                "gates": [gate.model_dump(mode="json", exclude={"output"}) for gate in gates],
            },
            separators=(",", ":"),
            sort_keys=True,
        )
    )


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


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_url(url: str, *, attempts: int = 60) -> bool:
    for _ in range(attempts):
        try:
            with urlopen(url, timeout=2) as response:
                if response.status == 200:
                    return True
        except OSError:
            pass
        time.sleep(2)
    return False


def _read_url(url: str) -> str:
    with urlopen(url, timeout=5) as response:
        return response.read(2_000_000).decode(errors="replace")
