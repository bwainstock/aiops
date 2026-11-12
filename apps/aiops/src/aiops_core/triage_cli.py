import argparse
import asyncio
import json
from pathlib import Path
from typing import cast
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from aiops_core.agent_audit import AgentAuditStore
from aiops_core.agent_models import AgentTask
from aiops_core.agent_policy import load_tool_profiles
from aiops_core.copilot_agent import CopilotAgentRunner
from aiops_core.database import Database
from aiops_core.evidence import EvidenceCollector
from aiops_core.intake import isoformat, utc_now
from aiops_core.models import DeploymentRecord, IncidentDetail
from aiops_core.triage_models import (
    StoredTriage,
    TriageHypothesis,
    TriageReview,
    TriageSubmission,
)
from aiops_core.triage_validation import validate_hypothesis, validate_review


class AiopsApiClient:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    def incident(self, incident_id: str) -> IncidentDetail:
        return IncidentDetail.model_validate(
            self._request("GET", f"/api/v1/incidents/{incident_id}")
        )

    def deployments(
        self,
        *,
        service: str,
        before: str,
    ) -> list[DeploymentRecord]:
        payload = self._request(
            "GET",
            "/api/v1/deployments?" + urlencode({"service": service, "before": before, "limit": 20}),
        )
        if not isinstance(payload, list):
            raise RuntimeError("deployment response is not a list")
        return [DeploymentRecord.model_validate(item) for item in cast(list[object], payload)]

    def store(self, submission: TriageSubmission) -> StoredTriage:
        return StoredTriage.model_validate(
            self._request(
                "POST",
                f"/api/v1/incidents/{submission.incident_id}/triage",
                submission.model_dump(mode="json"),
            )
        )

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, object] | None = None,
    ) -> object:
        data = (
            json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
            if payload is not None
            else None
        )
        request = Request(
            f"{self.base_url}{path}",
            data=data,
            method=method,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urlopen(request, timeout=30) as response:
                body = response.read(2_000_000)
        except HTTPError as error:
            detail = error.read(16_384).decode(errors="replace")
            raise RuntimeError(f"AIOps API returned HTTP {error.code}: {detail}") from error
        except URLError as error:
            raise RuntimeError(f"AIOps API request failed: {error.reason}") from error
        return json.loads(body)


async def triage_incident(
    *,
    incident_id: str,
    repository: Path,
    api_url: str,
    mcp_url: str,
    model: str,
) -> StoredTriage:
    api = AiopsApiClient(api_url)
    incident = api.incident(incident_id)
    latest_firing = next(
        event for event in reversed(incident.events) if event.kind == "alert.firing"
    )
    alert_payload = latest_firing.payload.get("alert")
    if not isinstance(alert_payload, dict):
        raise RuntimeError("latest firing event has no alert payload")
    alert_data = cast(dict[str, object], alert_payload)
    starts_at = alert_data.get("startsAt")
    if not isinstance(starts_at, str):
        raise RuntimeError("latest firing event has no start time")
    deployments = api.deployments(
        service=incident.incident.service,
        before=starts_at,
    )
    evidence = await EvidenceCollector(mcp_url).collect(incident, deployments)
    database = Database(repository / "data" / "triage-agent.db")
    database.initialize(applied_at=isoformat(utc_now()))
    runner = CopilotAgentRunner(
        audit_store=AgentAuditStore(database),
        profile=load_tool_profiles(repository / "config" / "tool-profiles.json").triage,
        repository=repository,
        base_directory=repository / "data" / "copilot-sdk",
        mcp_url=mcp_url,
        model=model,
    )
    hypothesis_run, hypothesis = await runner.run(
        AgentTask(
            profile="triage",
            prompt=_hypothesis_prompt(evidence.model_dump(mode="json")),
            timeout_seconds=240,
        ),
        TriageHypothesis,
    )
    hypothesis_errors = validate_hypothesis(evidence, hypothesis)
    review_run, review = await runner.run(
        AgentTask(
            profile="triage",
            prompt=_review_prompt(
                evidence.model_dump(mode="json"),
                hypothesis.model_dump(mode="json"),
                hypothesis_errors,
            ),
            timeout_seconds=240,
        ),
        TriageReview,
    )
    review_errors = validate_review(evidence, hypothesis_errors, review)
    validation_errors = hypothesis_errors + review_errors
    if validation_errors or review.verdict == "more_evidence":
        triage_status = "more_evidence"
    elif review.verdict == "no_action" or not hypothesis.remediation_justified:
        triage_status = "no_action"
    else:
        triage_status = "triaged"
    submission = TriageSubmission(
        incident_id=incident_id,
        evidence=evidence,
        hypothesis=hypothesis,
        review=review,
        status=triage_status,
        validation_errors=validation_errors,
        agent_runs=[hypothesis_run, review_run],
    )
    return api.store(submission)


def _hypothesis_prompt(evidence: dict[str, object]) -> str:
    return (
        "Analyze this frozen evidence bundle for the demo API error-rate incident. "
        "Every suspected cause must cite only evidence IDs present in the bundle. "
        "Rank causes contiguously from 1. List contradictions and missing evidence. "
        "Do not claim remediation is justified unless the symptom is reproduced and "
        "the cause is supported. Use read-only tools only to cross-check current facts; "
        "the structured conclusion must cite frozen evidence IDs.\n\n"
        + json.dumps(evidence, separators=(",", ":"), sort_keys=True)
    )


def _review_prompt(
    evidence: dict[str, object],
    hypothesis: dict[str, object],
    validation_errors: list[str],
) -> str:
    return (
        "Independently review this frozen evidence bundle and proposed hypothesis. "
        "Return approve only when every material claim is supported by cited evidence, "
        "the time ordering is coherent, and deterministic validation has no errors. "
        "Otherwise return more_evidence or no_action. Cite evidence IDs used by the "
        "review.\n\n"
        + json.dumps(
            {
                "evidence": evidence,
                "hypothesis": hypothesis,
                "deterministic_validation_errors": validation_errors,
            },
            separators=(",", ":"),
            sort_keys=True,
        )
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Triage one durable AIOps incident.")
    parser.add_argument("--incident-id", required=True)
    parser.add_argument("--repository", type=Path, default=Path.cwd())
    parser.add_argument("--api-url", default="http://127.0.0.1:8081")
    parser.add_argument("--mcp-url", default="http://127.0.0.1:8082/mcp")
    parser.add_argument("--model", default="auto")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = asyncio.run(
        triage_incident(
            incident_id=args.incident_id,
            repository=args.repository.resolve(),
            api_url=args.api_url,
            mcp_url=args.mcp_url,
            model=args.model,
        )
    )
    print(result.model_dump_json(indent=2))
