from collections.abc import Callable
from typing import TypeVar
from uuid import uuid4

from pydantic import BaseModel

from aiops_core.agent_audit import AgentAuditStore
from aiops_core.agent_models import AgentAuditEvent, AgentRunResult, AgentTask

OutputT = TypeVar("OutputT", bound=BaseModel)


class FakeAgentRunner:
    def __init__(
        self,
        audit_store: AgentAuditStore,
        responder: Callable[[AgentTask, type[BaseModel]], dict[str, object]],
    ) -> None:
        self.audit_store = audit_store
        self.responder = responder

    async def preflight(self) -> dict[str, object]:
        return {"authenticated": True, "provider": "fake"}

    async def run(
        self,
        task: AgentTask,
        response_model: type[OutputT],
    ) -> tuple[AgentRunResult, OutputT]:
        session_id = task.session_id or f"fake-{uuid4()}"
        run_id = self.audit_store.start(
            task=task,
            provider="fake",
            session_id=session_id,
        )
        self.audit_store.append(
            run_id,
            AgentAuditEvent(
                event_type="session.started",
                payload={"session_id": session_id, "profile": task.profile},
            ),
        )
        try:
            raw_output = self.responder(task, response_model)
            output = response_model.model_validate(raw_output)
            output_dict = output.model_dump(mode="json")
            self.audit_store.append(
                run_id,
                AgentAuditEvent(
                    event_type="assistant.structured",
                    payload=output_dict,
                ),
            )
            self.audit_store.complete(run_id, output_dict)
        except Exception as error:
            self.audit_store.complete(run_id, error)
            raise

        audit_events = self.audit_store.events(run_id)
        return (
            AgentRunResult(
                run_id=run_id,
                session_id=session_id,
                profile=task.profile,
                provider="fake",
                output=output_dict,
                audit_events=audit_events,
            ),
            output,
        )
