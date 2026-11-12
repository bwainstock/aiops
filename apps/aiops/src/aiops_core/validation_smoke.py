import argparse
import asyncio
import json
from pathlib import Path

from aiops_core.agent_audit import AgentAuditStore
from aiops_core.agent_policy import load_tool_profiles
from aiops_core.copilot_agent import CopilotAgentRunner
from aiops_core.database import Database
from aiops_core.intake import isoformat, utc_now
from aiops_core.remediation_models import RemediationCandidate
from aiops_core.validation import CandidateValidator, FileContentScenario


async def run_validation(
    *,
    repository: Path,
    candidate_record: Path,
) -> dict[str, object]:
    candidate = RemediationCandidate.model_validate_json(candidate_record.read_text())
    database = Database(repository / "data" / "validation-agent.db")
    database.initialize(applied_at=isoformat(utc_now()))
    reviewer = CopilotAgentRunner(
        audit_store=AgentAuditStore(database),
        profile=load_tool_profiles(repository / "config" / "tool-profiles.json").triage,
        repository=repository,
        base_directory=repository / "data" / "copilot-sdk",
        mcp_url="http://127.0.0.1:8082/mcp",
    )
    report = await CandidateValidator(
        repository=repository,
        reviewer=reviewer,
        worktrees_root=repository / "worktrees",
    ).validate(
        candidate,
        FileContentScenario(
            relative_path="scenarios/remediation_candidate.txt",
            expected_content="status=healthy\n",
        ),
    )
    report_path = repository / "data" / "validation-reports" / f"{candidate.id}.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report.model_dump_json(indent=2))
    result = report.model_dump(mode="json")
    result["record"] = str(report_path)
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate one isolated remediation candidate.")
    parser.add_argument("--candidate-record", type=Path, required=True)
    parser.add_argument("--repository", type=Path, default=Path.cwd())
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = asyncio.run(
        run_validation(
            repository=args.repository.resolve(),
            candidate_record=args.candidate_record.resolve(),
        )
    )
    print(json.dumps(result, indent=2, sort_keys=True))
