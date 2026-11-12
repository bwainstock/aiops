from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class RemediationModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RemediationRequest(RemediationModel):
    incident_id: str = Field(min_length=1)
    triage_status: Literal["triaged", "more_evidence", "no_action"]
    base_revision: str = Field(min_length=1)
    hypothesis_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    instructions: str = Field(min_length=1)
    allowed_paths: list[str] = Field(min_length=1)


class RemediationProposal(RemediationModel):
    summary: str = Field(min_length=1)
    changed_files: list[str] = Field(min_length=1)
    checks_run: list[str]
    residual_risks: list[str]


class RemediationCandidate(RemediationModel):
    id: str
    incident_id: str
    base_revision: str
    branch: str
    worktree_path: Path
    hypothesis_digest: str
    patch_digest: str
    changed_files: list[str]
    patch: str
    status: Literal["candidate"]
    proposal: RemediationProposal
