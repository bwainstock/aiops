import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from uuid import uuid4

from aiops_shared.alertmanager import Alert, AlertmanagerWebhook

from aiops_core.database import Database
from aiops_core.models import DeploymentEvent, DeploymentResult, IngestResult


def utc_now() -> datetime:
    return datetime.now(UTC)


def isoformat(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def canonical_json(value: object) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def event_key(prefix: str, value: object) -> str:
    payload = f"{prefix}:{canonical_json(value)}".encode()
    return hashlib.sha256(payload).hexdigest()


class IncidentIntake:
    def __init__(self, database: Database, *, job_max_attempts: int) -> None:
        self.database = database
        self.job_max_attempts = job_max_attempts

    def ingest_alertmanager(self, webhook: AlertmanagerWebhook) -> IngestResult:
        now = utc_now()
        now_text = isoformat(now)
        webhook_payload = webhook.model_dump(mode="json")
        raw_key = event_key("alertmanager-webhook", webhook_payload)
        accepted = 0
        duplicates = 0
        incident_ids: list[str] = []

        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO raw_events(
                        event_key, source, received_at, payload_json
                    ) VALUES (?, 'alertmanager', ?, ?)
                    """,
                    (raw_key, now_text, canonical_json(webhook_payload)),
                )
                for alert in webhook.alerts:
                    incident_id, was_duplicate = self._ingest_alert(
                        connection,
                        webhook=webhook,
                        alert=alert,
                        now_text=now_text,
                    )
                    if incident_id not in incident_ids:
                        incident_ids.append(incident_id)
                    if was_duplicate:
                        duplicates += 1
                    else:
                        accepted += 1
                connection.commit()
            except Exception:
                connection.rollback()
                raise

        return IngestResult(
            accepted_events=accepted,
            duplicate_events=duplicates,
            incident_ids=incident_ids,
        )

    def ingest_deployment(self, deployment: DeploymentEvent) -> DeploymentResult:
        now_text = isoformat(utc_now())
        payload = deployment.model_dump(mode="json")
        key = event_key("deployment", payload)
        deployment_id = str(uuid4())

        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                existing = connection.execute(
                    "SELECT id FROM deployments WHERE event_key = ?",
                    (key,),
                ).fetchone()
                if existing is not None:
                    connection.commit()
                    return DeploymentResult(id=existing["id"], duplicate=True)

                connection.execute(
                    """
                    INSERT INTO deployments(
                        id, event_key, service, environment, commit_sha, version,
                        deployed_at, metadata_json, received_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        deployment_id,
                        key,
                        deployment.service,
                        deployment.environment,
                        deployment.commit_sha.lower(),
                        deployment.version,
                        isoformat(deployment.deployed_at),
                        canonical_json(deployment.metadata),
                        now_text,
                    ),
                )
                self._audit(
                    connection,
                    incident_id=None,
                    action="deployment.recorded",
                    actor="intake",
                    now_text=now_text,
                    details={"deployment_id": deployment_id, **payload},
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise

        return DeploymentResult(id=deployment_id, duplicate=False)

    def _ingest_alert(
        self,
        connection: sqlite3.Connection,
        *,
        webhook: AlertmanagerWebhook,
        alert: Alert,
        now_text: str,
    ) -> tuple[str, bool]:
        alert_payload = {
            "alert": alert.model_dump(mode="json"),
            "group_labels": webhook.groupLabels,
            "common_labels": webhook.commonLabels,
            "common_annotations": webhook.commonAnnotations,
            "receiver": webhook.receiver,
        }
        key = event_key(
            "alert",
            {
                "fingerprint": alert.fingerprint,
                "status": alert.status,
                "starts_at": isoformat(alert.startsAt),
                "ends_at": isoformat(alert.endsAt),
            },
        )
        existing_event = connection.execute(
            """
            SELECT incident_id
            FROM incident_events
            WHERE event_key = ?
            """,
            (key,),
        ).fetchone()
        if existing_event is not None:
            return existing_event["incident_id"], True

        incident = connection.execute(
            "SELECT id FROM incidents WHERE fingerprint = ?",
            (alert.fingerprint,),
        ).fetchone()
        if incident is None:
            incident_id = str(uuid4())
            connection.execute(
                """
                INSERT INTO incidents(
                    id, fingerprint, alertname, service, severity, status,
                    workflow_state, opened_at, updated_at, resolved_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'new', ?, ?, ?)
                """,
                (
                    incident_id,
                    alert.fingerprint,
                    alert.labels.get("alertname", "unknown"),
                    alert.labels.get("service", "unknown"),
                    alert.labels.get("severity", "unknown"),
                    "open" if alert.status == "firing" else "resolved",
                    isoformat(alert.startsAt),
                    now_text,
                    self._resolved_at(alert),
                ),
            )
            self._audit(
                connection,
                incident_id=incident_id,
                action="incident.created",
                actor="intake",
                now_text=now_text,
                details={"fingerprint": alert.fingerprint},
            )
        else:
            incident_id = incident["id"]
            connection.execute(
                """
                UPDATE incidents
                SET alertname = ?, service = ?, severity = ?, status = ?,
                    updated_at = ?, resolved_at = ?
                WHERE id = ?
                """,
                (
                    alert.labels.get("alertname", "unknown"),
                    alert.labels.get("service", "unknown"),
                    alert.labels.get("severity", "unknown"),
                    "open" if alert.status == "firing" else "resolved",
                    now_text,
                    self._resolved_at(alert),
                    incident_id,
                ),
            )

        connection.execute(
            """
            INSERT INTO incident_events(
                incident_id, event_key, kind, occurred_at, payload_json
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                incident_id,
                key,
                f"alert.{alert.status}",
                now_text,
                canonical_json(alert_payload),
            ),
        )
        self._audit(
            connection,
            incident_id=incident_id,
            action=f"alert.{alert.status}",
            actor="alertmanager",
            now_text=now_text,
            details={"event_key": key, "fingerprint": alert.fingerprint},
        )

        if alert.status == "firing":
            connection.execute(
                """
                INSERT OR IGNORE INTO workflow_jobs(
                    incident_id, dedup_key, kind, state, attempts, max_attempts,
                    available_at, created_at, updated_at
                ) VALUES (?, ?, 'collect_evidence', 'pending', 0, ?, ?, ?, ?)
                """,
                (
                    incident_id,
                    f"collect_evidence:{key}",
                    self.job_max_attempts,
                    now_text,
                    now_text,
                    now_text,
                ),
            )

        return incident_id, False

    @staticmethod
    def _resolved_at(alert: Alert) -> str | None:
        if alert.status != "resolved" or alert.endsAt.year <= 1:
            return None
        return isoformat(alert.endsAt)

    @staticmethod
    def _audit(
        connection: sqlite3.Connection,
        *,
        incident_id: str | None,
        action: str,
        actor: str,
        now_text: str,
        details: object,
    ) -> None:
        connection.execute(
            """
            INSERT INTO audit_log(
                incident_id, action, actor, created_at, details_json
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (incident_id, action, actor, now_text, canonical_json(details)),
        )
