import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from aiops_shared.alertmanager import AlertmanagerWebhook
from fastapi import FastAPI, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse
from jinja2 import Environment, PackageLoader, select_autoescape

from aiops_core.approval_models import ApprovalDecision, ApprovalRequest, CandidateView
from aiops_core.candidate_repository import CandidateRepository
from aiops_core.config import Settings
from aiops_core.database import Database
from aiops_core.intake import IncidentIntake, isoformat, utc_now
from aiops_core.models import (
    DeploymentEvent,
    DeploymentRecord,
    DeploymentResult,
    HealthResponse,
    Incident,
    IncidentDetail,
    IngestResult,
    WorkflowJob,
)
from aiops_core.remediation_models import RemediationCandidate
from aiops_core.repository import IncidentRepository
from aiops_core.triage_models import StoredTriage, TriageSubmission
from aiops_core.triage_repository import TriageRepository
from aiops_core.triage_validation import validate_hypothesis, validate_review
from aiops_core.validation_models import ValidationReport
from aiops_core.workspace_merge import WorkspaceMerger


def create_app(settings: Settings | None = None) -> FastAPI:
    active_settings = settings or Settings.from_environment()
    database = Database(active_settings.database_path)
    intake = IncidentIntake(
        database,
        job_max_attempts=active_settings.job_max_attempts,
    )
    repository = IncidentRepository(database)
    triage_repository = TriageRepository(database)
    candidate_repository = CandidateRepository(database)
    templates = Environment(
        loader=PackageLoader("aiops_core", "templates"),
        autoescape=select_autoescape(),
    )
    repository_path = Path(os.environ.get("AIOPS_REPOSITORY_PATH", "/repository"))
    merger = WorkspaceMerger(
        repository=repository_path,
        worktrees_root=repository_path / "worktrees",
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None]:
        database.initialize(applied_at=isoformat(utc_now()))
        yield

    app = FastAPI(
        title="Local AIOps",
        version="0.1.0",
        lifespan=lifespan,
    )

    @app.get("/healthz", response_model=HealthResponse)
    def healthz() -> HealthResponse:
        database.healthcheck()
        return HealthResponse(status="ok")

    @app.post(
        "/api/v1/events/alertmanager",
        response_model=IngestResult,
        status_code=status.HTTP_202_ACCEPTED,
    )
    def ingest_alertmanager(webhook: AlertmanagerWebhook) -> IngestResult:
        return intake.ingest_alertmanager(webhook)

    @app.post(
        "/api/v1/events/deployments",
        response_model=DeploymentResult,
        status_code=status.HTTP_202_ACCEPTED,
    )
    def ingest_deployment(deployment: DeploymentEvent) -> DeploymentResult:
        return intake.ingest_deployment(deployment)

    @app.get("/api/v1/incidents", response_model=list[Incident])
    def list_incidents(
        incident_status: str | None = Query(default=None, alias="status"),
    ) -> list[Incident]:
        if incident_status not in {None, "open", "resolved"}:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="status must be open or resolved",
            )
        return repository.list_incidents(status=incident_status)

    @app.get("/api/v1/incidents/{incident_id}", response_model=IncidentDetail)
    def get_incident(incident_id: str) -> IncidentDetail:
        detail = repository.get_incident(incident_id)
        if detail is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="incident not found",
            )
        return detail

    @app.get("/api/v1/jobs", response_model=list[WorkflowJob])
    def list_jobs() -> list[WorkflowJob]:
        return repository.list_jobs()

    @app.get("/api/v1/deployments", response_model=list[DeploymentRecord])
    def list_deployments(
        service: str,
        before: str | None = None,
        limit: int = 20,
    ) -> list[DeploymentRecord]:
        try:
            return triage_repository.deployments(
                service=service,
                before=before,
                limit=limit,
            )
        except ValueError as error:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=str(error),
            ) from error

    @app.post(
        "/api/v1/incidents/{incident_id}/triage",
        response_model=StoredTriage,
        status_code=status.HTTP_201_CREATED,
    )
    def store_triage(
        incident_id: str,
        submission: TriageSubmission,
    ) -> StoredTriage:
        if submission.incident_id != incident_id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="incident ID does not match request path",
            )
        hypothesis_errors = validate_hypothesis(
            submission.evidence,
            submission.hypothesis,
        )
        review_errors = validate_review(
            submission.evidence,
            hypothesis_errors,
            submission.review,
        )
        if submission.validation_errors != hypothesis_errors + review_errors:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="validation errors do not match deterministic validation",
            )
        try:
            return triage_repository.store(submission)
        except ValueError as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(error),
            ) from error

    @app.get(
        "/api/v1/incidents/{incident_id}/triage",
        response_model=StoredTriage,
    )
    def get_latest_triage(incident_id: str) -> StoredTriage:
        triage = triage_repository.latest(incident_id)
        if triage is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="triage not found",
            )
        return triage

    @app.post("/api/v1/candidates", status_code=status.HTTP_201_CREATED)
    def store_candidate(candidate: RemediationCandidate) -> dict[str, str]:
        candidate_repository.store_candidate(candidate)
        return {"id": candidate.id}

    @app.post(
        "/api/v1/candidates/{candidate_id}/validation",
        status_code=status.HTTP_201_CREATED,
    )
    def store_validation(
        candidate_id: str,
        report: ValidationReport,
    ) -> dict[str, str]:
        if report.candidate_id != candidate_id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="candidate ID does not match request path",
            )
        try:
            candidate_repository.store_validation(report)
        except ValueError as error:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=str(error),
            ) from error
        return {"candidate_id": candidate_id, "validation_digest": report.digest}

    @app.get("/api/v1/candidates", response_model=list[CandidateView])
    def list_candidates() -> list[CandidateView]:
        return candidate_repository.list_views()

    @app.post(
        "/api/v1/candidates/{candidate_id}/decision",
        response_model=ApprovalDecision,
    )
    def decide_candidate(
        candidate_id: str,
        approval: ApprovalRequest,
    ) -> ApprovalDecision:
        candidate = candidate_repository.get_candidate(candidate_id)
        validation = candidate_repository.get_validation(candidate_id)
        if candidate is None or validation is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="candidate or validation report not found",
            )
        if approval.candidate_digest != candidate.patch_digest:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="candidate digest is stale",
            )
        if approval.validation_digest != validation.digest:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="validation digest is stale",
            )
        if validation.status != "passed":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="candidate validation did not pass",
            )
        merge_commit: str | None = None
        if approval.decision == "approve":
            try:
                merge_commit = merger.merge(candidate)
            except ValueError as error:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=str(error),
                ) from error
            except RuntimeError as error:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=str(error),
                ) from error
        return candidate_repository.record_decision(
            candidate_id=candidate_id,
            candidate_digest=approval.candidate_digest,
            validation_digest=approval.validation_digest,
            decision=approval.decision,
            reason=approval.reason,
            merge_commit=merge_commit,
        )

    @app.get("/dashboard", response_class=HTMLResponse)
    def dashboard(request: Request) -> HTMLResponse:
        template = templates.get_template("dashboard.html")
        return HTMLResponse(
            template.render(
                request=request,
                incidents=repository.list_incidents(),
                candidates=candidate_repository.list_views(),
            )
        )

    return app


app = create_app()
