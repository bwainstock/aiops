from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from aiops_core.agent_models import AgentRunResult


class ValidationModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GateResult(ValidationModel):
    name: str
    status: Literal["passed", "failed"]
    summary: str
    output: str = ""


class ValidationReview(ValidationModel):
    verdict: Literal["approve", "reject"]
    summary: str = Field(min_length=1)
    issues: list[str]


class ValidationReport(ValidationModel):
    candidate_id: str
    incident_id: str
    base_revision: str
    hypothesis_digest: str
    patch_digest: str
    gates: list[GateResult] = Field(min_length=1)
    review: ValidationReview
    review_run: AgentRunResult
    status: Literal["passed", "failed"]
    digest: str
