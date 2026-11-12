from pathlib import Path
from typing import TypeVar
from uuid import uuid4

from copilot import CopilotClient, ToolSet
from copilot.generated.rpc import PermissionDecisionUserNotAvailable
from copilot.generated.session_events import PermissionRequest
from copilot.session import PermissionInvocation
from copilot.session_events import AssistantMessageData
from copilot.tools import Tool, ToolInvocation, define_tool
from pydantic import BaseModel, ConfigDict, Field

from aiops_core.agent_audit import AgentAuditStore
from aiops_core.agent_models import AgentAuditEvent, AgentTask
from aiops_core.remediation_models import RemediationCandidate, RemediationProposal
from aiops_core.remediation_workspace import CheckName, RemediationWorkspace

OutputT = TypeVar("OutputT", bound=BaseModel)


class ReadFileParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1)


class WriteFileParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1)
    content: str


class RunCheckParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    check: CheckName


class EmptyParams(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RemediationTools:
    def __init__(
        self,
        *,
        workspace: RemediationWorkspace,
        audit_store: AgentAuditStore,
        run_id: str,
    ) -> None:
        self.workspace = workspace
        self.audit_store = audit_store
        self.run_id = run_id

    def tools(self) -> list[Tool]:
        return [
            define_tool(
                "read_candidate_file",
                description="Read one allowlisted file from the isolated candidate worktree.",
                params_type=ReadFileParams,
                handler=self.read_file,
                skip_permission=True,
                idempotent=True,
            ),
            define_tool(
                "write_candidate_file",
                description="Write one allowlisted file inside the isolated candidate worktree.",
                params_type=WriteFileParams,
                handler=self.write_file,
                skip_permission=True,
            ),
            define_tool(
                "run_candidate_check",
                description="Run one allowlisted validation command in the candidate worktree.",
                params_type=RunCheckParams,
                handler=self.run_check,
                skip_permission=True,
            ),
            define_tool(
                "candidate_diff",
                description="Read the current bounded candidate diff and changed file list.",
                params_type=EmptyParams,
                handler=self.diff,
                skip_permission=True,
                idempotent=True,
            ),
        ]

    def read_file(
        self,
        params: ReadFileParams,
        _invocation: ToolInvocation,
    ) -> dict[str, object]:
        content = self.workspace.read_file(params.path)
        self._audit("remediation.read", {"path": params.path})
        return {"path": params.path, "content": content}

    def write_file(
        self,
        params: WriteFileParams,
        _invocation: ToolInvocation,
    ) -> dict[str, object]:
        self.workspace.write_file(params.path, params.content)
        self._audit(
            "remediation.write",
            {"path": params.path, "bytes": len(params.content.encode())},
        )
        return {"path": params.path, "written": True}

    def run_check(
        self,
        params: RunCheckParams,
        _invocation: ToolInvocation,
    ) -> dict[str, object]:
        result = self.workspace.run_check(params.check)
        self._audit(
            "remediation.check",
            {
                "check": params.check,
                "success": result["success"],
                "exit_code": result["exit_code"],
            },
        )
        return result

    def diff(
        self,
        _params: EmptyParams,
        _invocation: ToolInvocation,
    ) -> dict[str, object]:
        patch, changed_files, patch_digest = self.workspace.patch()
        self._audit(
            "remediation.diff",
            {
                "changed_files": changed_files,
                "patch_digest": patch_digest,
            },
        )
        return {
            "patch": patch,
            "changed_files": changed_files,
            "patch_digest": patch_digest,
        }

    def _audit(self, event_type: str, payload: dict[str, object]) -> None:
        self.audit_store.append(
            self.run_id,
            AgentAuditEvent(event_type=event_type, payload=payload),
        )


class CopilotRemediationAgent:
    def __init__(
        self,
        *,
        audit_store: AgentAuditStore,
        repository: Path,
        base_directory: Path,
        model: str = "auto",
    ) -> None:
        self.audit_store = audit_store
        self.repository = repository.resolve()
        self.base_directory = base_directory.resolve()
        self.model = model

    async def run(
        self,
        workspace: RemediationWorkspace,
    ) -> RemediationCandidate:
        workspace.create()
        session_id = str(uuid4())
        task = AgentTask(
            profile="remediation",
            prompt=workspace.request.instructions,
        )
        run_id = self.audit_store.start(
            task=task,
            provider="github-copilot-sdk",
            session_id=session_id,
        )
        tool_provider = RemediationTools(
            workspace=workspace,
            audit_store=self.audit_store,
            run_id=run_id,
        )
        try:
            async with CopilotClient(
                working_directory=str(workspace.path),
                base_directory=str(self.base_directory),
                use_logged_in_user=True,
                log_level="warning",
            ) as client:
                session = await client.create_session(
                    session_id=session_id,
                    on_permission_request=self._deny_permission,
                    model=self.model,
                    client_name="local-aiops-remediation",
                    tools=tool_provider.tools(),
                    available_tools=ToolSet().add_custom("*"),
                    excluded_tools=ToolSet().add_builtin("*").add_mcp("*"),
                    system_message={
                        "mode": "append",
                        "content": (
                            "You are editing only an isolated remediation worktree. "
                            "Use the supplied custom tools; no other tools are allowed. "
                            "Keep the patch minimal, inspect the final diff, and return "
                            "the exact changed file list. Write summary as a concise "
                            "PR-style explanation of the problem and fix, record every "
                            "check you ran, and list any residual risks."
                        ),
                    },
                    working_directory=str(workspace.path),
                    session_limits={"max_ai_credits": 30},
                    disabled_mcp_servers=["github-mcp-server", "githubiq"],
                    enable_config_discovery=False,
                    enable_file_hooks=False,
                    enable_host_git_operations=False,
                    enable_skills=False,
                    skip_custom_instructions=True,
                )
                async with session:
                    response = await session.send_and_wait(
                        workspace.request.instructions,
                        source="system",
                        response_schema=RemediationProposal,
                        timeout=240,
                    )
            if response is None or not isinstance(response.data, AssistantMessageData):
                raise RuntimeError("Copilot returned no remediation proposal")
            proposal = RemediationProposal.model_validate_json(response.data.content)
            candidate = workspace.finalize(proposal)
            self.audit_store.append(
                run_id,
                AgentAuditEvent(
                    event_type="remediation.candidate",
                    payload={
                        "candidate_id": candidate.id,
                        "patch_digest": candidate.patch_digest,
                        "changed_files": candidate.changed_files,
                    },
                ),
            )
            self.audit_store.complete(
                run_id,
                candidate.model_dump(mode="json"),
            )
            return candidate
        except Exception as error:
            self.audit_store.complete(run_id, error)
            raise

    def _deny_permission(
        self,
        _request: PermissionRequest,
        _invocation: PermissionInvocation,
    ) -> PermissionDecisionUserNotAvailable:
        return PermissionDecisionUserNotAvailable()
