from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from aiops_core.remediation_models import RemediationCandidate
from aiops_core.validation_models import ValidationReport


class ApprovalModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ApprovalRequest(ApprovalModel):
    decision: Literal["approve", "reject"]
    candidate_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    validation_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    reason: str = Field(min_length=1, max_length=2_000)


class ApprovalDecision(ApprovalRequest):
    id: str
    candidate_id: str
    merge_commit: str | None
    created_at: datetime


class CandidateView(ApprovalModel):
    candidate: RemediationCandidate
    validation: ValidationReport | None
    decision: ApprovalDecision | None
