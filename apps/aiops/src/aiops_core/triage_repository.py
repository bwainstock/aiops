import json
from uuid import uuid4

from aiops_core.database import Database
from aiops_core.intake import canonical_json, isoformat, utc_now
from aiops_core.models import DeploymentRecord
from aiops_core.triage_models import StoredTriage, TriageSubmission


class TriageRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def deployments(
        self,
        *,
        service: str,
        before: str | None = None,
        limit: int = 20,
    ) -> list[DeploymentRecord]:
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        query = """
            SELECT id, service, environment, commit_sha, version, deployed_at,
                   metadata_json, received_at
            FROM deployments
            WHERE service = ?
        """
        parameters: list[object] = [service]
        if before is not None:
            query += " AND deployed_at <= ?"
            parameters.append(before)
        query += " ORDER BY deployed_at DESC LIMIT ?"
        parameters.append(limit)
        with self.database.connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [
            DeploymentRecord(
                id=row["id"],
                service=row["service"],
                environment=row["environment"],
                commit_sha=row["commit_sha"],
                version=row["version"],
                deployed_at=row["deployed_at"],
                metadata=json.loads(row["metadata_json"]),
                received_at=row["received_at"],
            )
            for row in rows
        ]

    def store(self, submission: TriageSubmission) -> StoredTriage:
        record_id = str(uuid4())
        created_at = utc_now()
        payload = submission.model_dump(mode="json")
        with self.database.connect() as connection:
            incident = connection.execute(
                "SELECT id FROM incidents WHERE id = ?",
                (submission.incident_id,),
            ).fetchone()
            if incident is None:
                raise ValueError("incident does not exist")
            connection.execute(
                """
                INSERT INTO triage_records(
                    id, incident_id, status, evidence_digest, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    record_id,
                    submission.incident_id,
                    submission.status,
                    submission.evidence.digest,
                    canonical_json(payload),
                    isoformat(created_at),
                ),
            )
            connection.execute(
                """
                UPDATE incidents
                SET workflow_state = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    submission.status,
                    isoformat(created_at),
                    submission.incident_id,
                ),
            )
            connection.execute(
                """
                INSERT INTO audit_log(
                    incident_id, action, actor, created_at, details_json
                ) VALUES (?, 'triage.stored', 'triage', ?, ?)
                """,
                (
                    submission.incident_id,
                    isoformat(created_at),
                    canonical_json(
                        {
                            "triage_id": record_id,
                            "status": submission.status,
                            "evidence_digest": submission.evidence.digest,
                        }
                    ),
                ),
            )
        return StoredTriage(
            id=record_id,
            created_at=created_at,
            **submission.model_dump(),
        )

    def latest(self, incident_id: str) -> StoredTriage | None:
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT id, payload_json, created_at
                FROM triage_records
                WHERE incident_id = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (incident_id,),
            ).fetchone()
        if row is None:
            return None
        return StoredTriage(
            id=row["id"],
            created_at=row["created_at"],
            **json.loads(row["payload_json"]),
        )
