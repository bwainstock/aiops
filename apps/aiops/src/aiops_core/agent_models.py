from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class AgentModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AgentTask(AgentModel):
    profile: Literal["triage", "review", "remediation"]
    prompt: str = Field(min_length=1)
    incident_id: str | None = None
    session_id: str | None = None
    max_ai_credits: float = Field(default=30, ge=30)
    timeout_seconds: float = Field(default=120, gt=0)


class AgentAuditEvent(AgentModel):
    event_type: str
    payload: dict[str, object]


class AgentRunResult(AgentModel):
    run_id: str
    session_id: str
    profile: str
    provider: str
    output: dict[str, object]
    audit_events: list[AgentAuditEvent]
