from typing import Protocol, TypeVar

from pydantic import BaseModel

from aiops_core.agent_models import AgentRunResult, AgentTask

OutputT = TypeVar("OutputT", bound=BaseModel)


class AgentRunner(Protocol):
    async def preflight(self) -> dict[str, object]: ...

    async def run(
        self,
        task: AgentTask,
        response_model: type[OutputT],
    ) -> tuple[AgentRunResult, OutputT]: ...
