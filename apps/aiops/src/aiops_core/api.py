from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from aiops_shared.alertmanager import AlertmanagerWebhook
from fastapi import FastAPI, HTTPException, Query, status

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
from aiops_core.repository import IncidentRepository
from aiops_core.triage_models import StoredTriage, TriageSubmission
from aiops_core.triage_repository import TriageRepository
from aiops_core.triage_validation import validate_hypothesis, validate_review


def create_app(settings: Settings | None = None) -> FastAPI:
    active_settings = settings or Settings.from_environment()
    database = Database(active_settings.database_path)
    intake = IncidentIntake(
        database,
        job_max_attempts=active_settings.job_max_attempts,
    )
    repository = IncidentRepository(database)
    triage_repository = TriageRepository(database)

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

    return app


app = create_app()
