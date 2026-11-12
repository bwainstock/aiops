import argparse
import asyncio
import hashlib
import json
from pathlib import Path

from aiops_core.agent_audit import AgentAuditStore
from aiops_core.database import Database
from aiops_core.intake import isoformat, utc_now
from aiops_core.remediation_agent import CopilotRemediationAgent
from aiops_core.remediation_models import RemediationRequest
from aiops_core.remediation_workspace import RemediationWorkspace


async def run_smoke(repository: Path) -> dict[str, object]:
    data_directory = repository / "data"
    database = Database(data_directory / "remediation-agent.db")
    database.initialize(applied_at=isoformat(utc_now()))
    request = RemediationRequest(
        incident_id="stage10-remediation-exercise",
        triage_status="triaged",
        base_revision=_git_head(repository),
        hypothesis_digest=hashlib.sha256(b"approved-stage10-remediation-exercise").hexdigest(),
        instructions=(
            "Create exactly one new file at scenarios/remediation_candidate.txt "
            "with exactly this content: status=healthy followed by a newline. "
            "Then inspect candidate_diff. Return a concise summary, changed_files "
            "containing exactly scenarios/remediation_candidate.txt, no checks_run, "
            "and no residual_risks."
        ),
        allowed_paths=["scenarios/**"],
    )
    workspace = RemediationWorkspace(
        repository=repository,
        worktrees_root=repository / "worktrees",
        request=request,
    )
    candidate = await CopilotRemediationAgent(
        audit_store=AgentAuditStore(database),
        repository=repository,
        base_directory=data_directory / "copilot-sdk",
    ).run(workspace)
    if (repository / "scenarios/remediation_candidate.txt").exists():
        raise RuntimeError("main working tree was modified")
    candidate_path = data_directory / "remediation-candidates" / f"{candidate.id}.json"
    candidate_path.parent.mkdir(parents=True, exist_ok=True)
    candidate_path.write_text(candidate.model_dump_json(indent=2))
    return {
        "candidate_id": candidate.id,
        "branch": candidate.branch,
        "worktree_path": str(candidate.worktree_path),
        "base_revision": candidate.base_revision,
        "hypothesis_digest": candidate.hypothesis_digest,
        "patch_digest": candidate.patch_digest,
        "changed_files": candidate.changed_files,
        "patch": candidate.patch,
        "record": str(candidate_path),
    }


def _git_head(repository: Path) -> str:
    import subprocess

    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repository,
        capture_output=True,
        check=True,
        text=True,
    )
    return completed.stdout.strip()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the isolated Copilot remediation smoke test.")
    parser.add_argument("--repository", type=Path, default=Path.cwd())
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = asyncio.run(run_smoke(args.repository.resolve()))
    print(json.dumps(result, indent=2, sort_keys=True))
