import asyncio
from pathlib import Path

import pytest
from aiops_core.agent_audit import AgentAuditStore
from aiops_core.agent_models import AgentTask
from aiops_core.agent_policy import load_tool_profiles
from aiops_core.copilot_agent import CopilotAgentRunner
from aiops_core.database import Database
from aiops_core.fake_agent import FakeAgentRunner
from aiops_core.intake import isoformat, utc_now
from copilot.generated.rpc import (
    PermissionDecisionApproveOnce,
    PermissionDecisionUserNotAvailable,
)
from copilot.generated.session_events import (
    PermissionRequestMcp,
    PermissionRequestShell,
)
from pydantic import BaseModel, ConfigDict, ValidationError


class ProbeResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer: int
    evidence: str


def audit_store(tmp_path: Path) -> AgentAuditStore:
    database = Database(tmp_path / "aiops.db")
    database.initialize(applied_at=isoformat(utc_now()))
    return AgentAuditStore(database)


def test_fake_agent_returns_typed_output_and_persists_audit(tmp_path: Path) -> None:
    store = audit_store(tmp_path)
    runner = FakeAgentRunner(
        store,
        lambda task, response_model: {
            "answer": len(task.prompt),
            "evidence": response_model.__name__,
        },
    )

    result, output = asyncio.run(
        runner.run(
            AgentTask(profile="triage", prompt="four"),
            ProbeResult,
        )
    )

    assert output == ProbeResult(answer=4, evidence="ProbeResult")
    assert result.provider == "fake"
    assert [event.event_type for event in result.audit_events] == [
        "session.started",
        "assistant.structured",
    ]


def test_fake_agent_rejects_invalid_structured_output(tmp_path: Path) -> None:
    runner = FakeAgentRunner(
        audit_store(tmp_path),
        lambda _task, _response_model: {"answer": "not-an-integer"},
    )

    with pytest.raises(ValidationError):
        asyncio.run(
            runner.run(
                AgentTask(profile="triage", prompt="invalid"),
                ProbeResult,
            )
        )


def test_agent_task_rejects_unsupported_credit_limit() -> None:
    with pytest.raises(ValidationError):
        AgentTask(
            profile="triage",
            prompt="invalid budget",
            max_ai_credits=29,
        )


def test_copilot_permission_and_hook_policy_is_deny_by_default(
    tmp_path: Path,
) -> None:
    store = audit_store(tmp_path)
    profile = load_tool_profiles(Path(__file__).parents[1] / "config" / "tool-profiles.json").triage
    runner = CopilotAgentRunner(
        audit_store=store,
        profile=profile,
        repository=Path(__file__).parents[1],
        base_directory=tmp_path / "copilot",
        mcp_url="http://127.0.0.1:8082/mcp",
    )
    run_id = store.start(
        task=AgentTask(profile="triage", prompt="policy"),
        provider="test",
        session_id="test-session",
    )

    allowed_request = PermissionRequestMcp(
        read_only=True,
        server_name="local-ops",
        tool_name="prometheus_query",
        tool_title="Prometheus query",
        args={"query": "up"},
        permission_recommendation=None,
        server_display_name=None,
        tool_call_id="call-1",
        managed_approval_required=None,
    )
    denied_write_request = PermissionRequestMcp(
        read_only=False,
        server_name="local-ops",
        tool_name="prometheus_query",
        tool_title="Prometheus query",
        args={"query": "up"},
        permission_recommendation=None,
        server_display_name=None,
        tool_call_id="call-2",
        managed_approval_required=None,
    )
    denied_shell_request = PermissionRequestShell(
        can_offer_session_approval=False,
        commands=[],
        full_command_text="echo unsafe",
        has_write_file_redirection=False,
        intention="unsafe",
        possible_paths=[],
        possible_urls=[],
        command_segments=None,
        managed_approval_required=None,
        request_sandbox_bypass=None,
        request_sandbox_bypass_reason=None,
        request_sandbox_permissive=None,
        resolved_paths=None,
        resolved_working_directory=None,
        tool_call_id="call-3",
        warning=None,
        sandbox_path_grant=None,
    )

    assert isinstance(
        runner.permission_handler(allowed_request, {"session_id": "session"}),
        PermissionDecisionApproveOnce,
    )
    assert isinstance(
        runner.permission_handler(denied_write_request, {"session_id": "session"}),
        PermissionDecisionUserNotAvailable,
    )
    assert isinstance(
        runner.permission_handler(denied_shell_request, {"session_id": "session"}),
        PermissionDecisionUserNotAvailable,
    )

    hook = runner.pre_tool_hook(run_id)
    assert hook(
        {
            "sessionId": "session",
            "timestamp": utc_now(),
            "workingDirectory": str(Path(__file__).parents[1]),
            "toolName": "prometheus_query",
            "toolArgs": {"query": "up"},
        },
        {},
    ) == {"permissionDecision": "allow"}
    denied = hook(
        {
            "sessionId": "session",
            "timestamp": utc_now(),
            "workingDirectory": str(Path(__file__).parents[1]),
            "toolName": "shell",
            "toolArgs": {"command": "echo unsafe"},
        },
        {},
    )
    assert denied.get("permissionDecision") == "deny"
    assert [event.payload["decision"] for event in store.events(run_id)] == [
        "allow",
        "deny",
    ]
