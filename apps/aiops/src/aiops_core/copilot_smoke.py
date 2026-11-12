import argparse
import asyncio
import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from aiops_core.agent_audit import AgentAuditStore
from aiops_core.agent_models import AgentTask
from aiops_core.agent_policy import load_tool_profiles
from aiops_core.copilot_agent import CopilotAgentRunner
from aiops_core.database import Database
from aiops_core.intake import isoformat, utc_now


class CopilotSmokeResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_up: bool
    evidence: str


async def run_smoke(
    repository: Path,
    mcp_url: str,
    *,
    preflight_only: bool = False,
) -> dict[str, object]:
    data_directory = repository / "data"
    database = Database(data_directory / "copilot-smoke.db")
    database.initialize(applied_at=isoformat(utc_now()))
    runner = CopilotAgentRunner(
        audit_store=AgentAuditStore(database),
        profile=load_tool_profiles(repository / "config" / "tool-profiles.json").triage,
        repository=repository,
        base_directory=data_directory / "copilot-sdk",
        mcp_url=mcp_url,
    )
    preflight = await runner.preflight()
    if preflight_only:
        return {"preflight": preflight}
    first, first_output = await runner.run(
        AgentTask(
            profile="triage",
            prompt=(
                "Call the Prometheus investigation tool with exact query "
                'up{job="demo-api"}. Set target_up true only if its current value '
                "is 1, and cite the exact result in evidence."
            ),
            timeout_seconds=180,
        ),
        CopilotSmokeResult,
    )
    resumed, resumed_output = await runner.run(
        AgentTask(
            profile="triage",
            session_id=first.session_id,
            prompt=(
                "Return the same target health conclusion from the existing "
                "investigation context. Re-query only if needed."
            ),
            timeout_seconds=180,
        ),
        CopilotSmokeResult,
    )
    if not first_output.target_up or not resumed_output.target_up:
        raise RuntimeError("Copilot did not verify the demo API target as up")
    if first.session_id != resumed.session_id:
        raise RuntimeError("Copilot session was not resumed")
    if not any(
        event.event_type == "tool.permission" and event.payload.get("decision") == "allow"
        for event in first.audit_events
    ):
        raise RuntimeError("live run did not record an allowed tool decision")
    if not any(event.event_type == "tool.completed" for event in first.audit_events):
        raise RuntimeError("live run did not record tool completion")

    return {
        "preflight": preflight,
        "session_id": first.session_id,
        "first": first_output.model_dump(mode="json"),
        "resumed": resumed_output.model_dump(mode="json"),
        "first_audit_events": [event.event_type for event in first.audit_events],
        "resumed_audit_events": [event.event_type for event in resumed.audit_events],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the live Copilot SDK smoke test.")
    parser.add_argument(
        "--repository",
        type=Path,
        default=Path.cwd(),
    )
    parser.add_argument(
        "--mcp-url",
        default="http://127.0.0.1:8082/mcp",
    )
    parser.add_argument("--preflight-only", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = asyncio.run(
        run_smoke(
            args.repository.resolve(),
            args.mcp_url,
            preflight_only=args.preflight_only,
        )
    )
    print(json.dumps(result, indent=2, sort_keys=True))
