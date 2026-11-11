from typing import cast

from aiops_core.database import Database, json_object
from aiops_core.models import (
    AuditEntry,
    Incident,
    IncidentDetail,
    IncidentEvent,
    WorkflowJob,
)


class IncidentRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def list_incidents(self, *, status: str | None = None) -> list[Incident]:
        query = "SELECT * FROM incidents"
        parameters: tuple[str, ...] = ()
        if status is not None:
            query += " WHERE status = ?"
            parameters = (status,)
        query += " ORDER BY updated_at DESC, id"
        with self.database.connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [Incident.model_validate(dict(row)) for row in rows]

    def get_incident(self, incident_id: str) -> IncidentDetail | None:
        with self.database.connect() as connection:
            incident_row = connection.execute(
                "SELECT * FROM incidents WHERE id = ?",
                (incident_id,),
            ).fetchone()
            if incident_row is None:
                return None
            event_rows = connection.execute(
                """
                SELECT id, kind, occurred_at, payload_json
                FROM incident_events
                WHERE incident_id = ?
                ORDER BY id
                """,
                (incident_id,),
            ).fetchall()
            job_rows = connection.execute(
                """
                SELECT id, incident_id, kind, state, attempts, max_attempts,
                       available_at, lease_owner, lease_expires_at, last_error,
                       created_at, updated_at
                FROM workflow_jobs
                WHERE incident_id = ?
                ORDER BY id
                """,
                (incident_id,),
            ).fetchall()
            audit_rows = connection.execute(
                """
                SELECT id, action, actor, created_at, details_json
                FROM audit_log
                WHERE incident_id = ?
                ORDER BY id
                """,
                (incident_id,),
            ).fetchall()

        return IncidentDetail(
            incident=Incident.model_validate(dict(incident_row)),
            events=[
                IncidentEvent(
                    id=row["id"],
                    kind=row["kind"],
                    occurred_at=row["occurred_at"],
                    payload=json_object(row["payload_json"]),
                )
                for row in event_rows
            ],
            jobs=[WorkflowJob.model_validate(dict(row)) for row in job_rows],
            audit=[
                AuditEntry(
                    id=row["id"],
                    action=row["action"],
                    actor=row["actor"],
                    created_at=row["created_at"],
                    details=json_object(row["details_json"]),
                )
                for row in audit_rows
            ],
        )

    def list_jobs(self) -> list[WorkflowJob]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT id, incident_id, kind, state, attempts, max_attempts,
                       available_at, lease_owner, lease_expires_at, last_error,
                       created_at, updated_at
                FROM workflow_jobs
                ORDER BY id
                """
            ).fetchall()
        return [WorkflowJob.model_validate(dict(row)) for row in rows]

    def count_rows(self, table: str) -> int:
        allowed = {
            "audit_log",
            "deployments",
            "incident_events",
            "incidents",
            "raw_events",
            "workflow_jobs",
        }
        if table not in allowed:
            raise ValueError("table is not queryable")
        with self.database.connect() as connection:
            row = connection.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()
        if row is None:
            raise RuntimeError("count query returned no row")
        return cast(int, row["count"])
