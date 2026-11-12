from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from aiops_core.agent_models import AgentRunResult

EvidenceSource = Literal[
    "alertmanager",
    "prometheus",
    "loki",
    "git",
    "docker",
    "deployment",
]


class TriageModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EvidenceItem(TriageModel):
    id: str
    source: EvidenceSource
    kind: str
    observed_at: datetime
    query: str | None = None
    content: dict[str, object]
    digest: str


class EvidenceBundle(TriageModel):
    id: str
    incident_id: str
    collected_at: datetime
    window_start: datetime
    window_end: datetime
    items: list[EvidenceItem] = Field(min_length=1)
    digest: str


class SuspectedCause(TriageModel):
    rank: int = Field(ge=1)
    title: str = Field(min_length=1)
    explanation: str = Field(min_length=1)
    evidence_ids: list[str] = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)


class TriageHypothesis(TriageModel):
    symptom_summary: str = Field(min_length=1)
    suspected_causes: list[SuspectedCause] = Field(min_length=1)
    contradictory_evidence_ids: list[str]
    missing_evidence: list[str]
    falsification_checks: list[str] = Field(min_length=1)
    remediation_justified: bool


class TriageReview(TriageModel):
    verdict: Literal["approve", "more_evidence", "no_action"]
    summary: str = Field(min_length=1)
    issues: list[str]
    evidence_ids: list[str]


class TriageSubmission(TriageModel):
    incident_id: str
    evidence: EvidenceBundle
    hypothesis: TriageHypothesis
    review: TriageReview
    status: Literal["triaged", "more_evidence", "no_action"]
    validation_errors: list[str]
    agent_runs: list[AgentRunResult]


class StoredTriage(TriageSubmission):
    id: str
    created_at: datetime
