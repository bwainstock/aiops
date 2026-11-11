from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

JsonScalar = str | int | float | bool | None


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HealthResponse(ApiModel):
    status: Literal["ok"]


class IngestResult(ApiModel):
    accepted_events: int = Field(ge=0)
    duplicate_events: int = Field(ge=0)
    incident_ids: list[str]


class DeploymentEvent(ApiModel):
    service: str = Field(min_length=1, max_length=100)
    environment: str = Field(min_length=1, max_length=100)
    commit_sha: str = Field(pattern=r"^[0-9a-fA-F]{7,64}$")
    version: str | None = Field(default=None, max_length=100)
    deployed_at: datetime
    metadata: dict[str, JsonScalar] = Field(default_factory=dict)


class DeploymentResult(ApiModel):
    id: str
    duplicate: bool


class Incident(ApiModel):
    id: str
    fingerprint: str
    alertname: str
    service: str
    severity: str
    status: Literal["open", "resolved"]
    workflow_state: str
    opened_at: datetime
    updated_at: datetime
    resolved_at: datetime | None


class IncidentEvent(ApiModel):
    id: int
    kind: str
    occurred_at: datetime
    payload: dict[str, object]


class WorkflowJob(ApiModel):
    id: int
    incident_id: str
    kind: str
    state: Literal["pending", "leased", "done", "failed"]
    attempts: int
    max_attempts: int
    available_at: datetime
    lease_owner: str | None
    lease_expires_at: datetime | None
    last_error: str | None
    created_at: datetime
    updated_at: datetime


class AuditEntry(ApiModel):
    id: int
    action: str
    actor: str
    created_at: datetime
    details: dict[str, object]


class IncidentDetail(ApiModel):
    incident: Incident
    events: list[IncidentEvent]
    jobs: list[WorkflowJob]
    audit: list[AuditEntry]
