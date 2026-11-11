import signal
from dataclasses import dataclass
from datetime import datetime, timedelta
from threading import Event
from uuid import uuid4

from aiops_core.config import Settings
from aiops_core.database import Database
from aiops_core.intake import canonical_json, isoformat, utc_now


@dataclass(frozen=True)
class ClaimedJob:
    id: int
    incident_id: str
    kind: str
    attempts: int
    max_attempts: int
    lease_owner: str


class WorkflowQueue:
    def __init__(self, database: Database, *, lease_seconds: float) -> None:
        self.database = database
        self.lease_seconds = lease_seconds

    def claim_next(
        self,
        *,
        worker_id: str,
        now: datetime | None = None,
    ) -> ClaimedJob | None:
        claimed_at = now or utc_now()
        now_text = isoformat(claimed_at)
        lease_expires_at = isoformat(claimed_at + timedelta(seconds=self.lease_seconds))

        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                expired = connection.execute(
                    """
                    SELECT id, incident_id, attempts, max_attempts
                    FROM workflow_jobs
                    WHERE state = 'leased' AND lease_expires_at <= ?
                    """,
                    (now_text,),
                ).fetchall()
                for row in expired:
                    terminal = row["attempts"] >= row["max_attempts"]
                    connection.execute(
                        """
                        UPDATE workflow_jobs
                        SET state = ?, lease_owner = NULL,
                            lease_expires_at = NULL,
                            last_error = CASE
                                WHEN ? THEN 'lease expired after final attempt'
                                ELSE last_error
                            END,
                            updated_at = ?
                        WHERE id = ?
                        """,
                        (
                            "failed" if terminal else "pending",
                            terminal,
                            now_text,
                            row["id"],
                        ),
                    )
                    connection.execute(
                        """
                        INSERT INTO audit_log(
                            incident_id, action, actor, created_at, details_json
                        ) VALUES (?, 'job.lease_expired', 'workflow', ?, ?)
                        """,
                        (
                            row["incident_id"],
                            now_text,
                            canonical_json(
                                {
                                    "job_id": row["id"],
                                    "attempts": row["attempts"],
                                    "terminal": terminal,
                                }
                            ),
                        ),
                    )
                    if terminal:
                        connection.execute(
                            """
                            INSERT INTO audit_log(
                                incident_id, action, actor, created_at, details_json
                            ) VALUES (?, 'job.failed', 'workflow', ?, ?)
                            """,
                            (
                                row["incident_id"],
                                now_text,
                                canonical_json(
                                    {
                                        "job_id": row["id"],
                                        "error": "lease expired after final attempt",
                                    }
                                ),
                            ),
                        )
                row = connection.execute(
                    """
                    SELECT id, incident_id, kind, attempts, max_attempts
                    FROM workflow_jobs
                    WHERE state = 'pending'
                      AND available_at <= ?
                      AND attempts < max_attempts
                    ORDER BY id
                    LIMIT 1
                    """,
                    (now_text,),
                ).fetchone()
                if row is None:
                    connection.commit()
                    return None

                attempts = row["attempts"] + 1
                connection.execute(
                    """
                    UPDATE workflow_jobs
                    SET state = 'leased', attempts = ?, lease_owner = ?,
                        lease_expires_at = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (attempts, worker_id, lease_expires_at, now_text, row["id"]),
                )
                connection.execute(
                    """
                    INSERT INTO audit_log(
                        incident_id, action, actor, created_at, details_json
                    ) VALUES (?, 'job.leased', ?, ?, ?)
                    """,
                    (
                        row["incident_id"],
                        worker_id,
                        now_text,
                        canonical_json({"job_id": row["id"], "attempt": attempts}),
                    ),
                )
                connection.commit()
                return ClaimedJob(
                    id=row["id"],
                    incident_id=row["incident_id"],
                    kind=row["kind"],
                    attempts=attempts,
                    max_attempts=row["max_attempts"],
                    lease_owner=worker_id,
                )
            except Exception:
                connection.rollback()
                raise

    def complete(self, job: ClaimedJob, *, now: datetime | None = None) -> None:
        completed_at = now or utc_now()
        now_text = isoformat(completed_at)
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                updated = connection.execute(
                    """
                    UPDATE workflow_jobs
                    SET state = 'done', lease_owner = NULL,
                        lease_expires_at = NULL, updated_at = ?
                    WHERE id = ? AND state = 'leased' AND lease_owner = ?
                    """,
                    (now_text, job.id, job.lease_owner),
                )
                if updated.rowcount != 1:
                    raise RuntimeError("job lease is no longer owned by this worker")
                connection.execute(
                    """
                    UPDATE incidents
                    SET workflow_state = 'collecting', updated_at = ?
                    WHERE id = ?
                    """,
                    (now_text, job.incident_id),
                )
                connection.execute(
                    """
                    INSERT OR IGNORE INTO incident_events(
                        incident_id, event_key, kind, occurred_at, payload_json
                    ) VALUES (?, ?, 'workflow.collecting', ?, ?)
                    """,
                    (
                        job.incident_id,
                        f"workflow.collecting:{job.id}",
                        now_text,
                        canonical_json({"job_id": job.id, "attempt": job.attempts}),
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO audit_log(
                        incident_id, action, actor, created_at, details_json
                    ) VALUES (?, 'job.completed', ?, ?, ?)
                    """,
                    (
                        job.incident_id,
                        job.lease_owner,
                        now_text,
                        canonical_json({"job_id": job.id}),
                    ),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def fail(
        self,
        job: ClaimedJob,
        *,
        error: str,
        now: datetime | None = None,
    ) -> None:
        failed_at = now or utc_now()
        now_text = isoformat(failed_at)
        terminal = job.attempts >= job.max_attempts
        next_state = "failed" if terminal else "pending"
        available_at = isoformat(failed_at + timedelta(seconds=min(2**job.attempts, 30)))
        with self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    """
                    UPDATE workflow_jobs
                    SET state = ?, available_at = ?, lease_owner = NULL,
                        lease_expires_at = NULL, last_error = ?, updated_at = ?
                    WHERE id = ? AND state = 'leased' AND lease_owner = ?
                    """,
                    (
                        next_state,
                        available_at,
                        error,
                        now_text,
                        job.id,
                        job.lease_owner,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO audit_log(
                        incident_id, action, actor, created_at, details_json
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        job.incident_id,
                        "job.failed" if terminal else "job.retry_scheduled",
                        job.lease_owner,
                        now_text,
                        canonical_json({"job_id": job.id, "error": error}),
                    ),
                )
                connection.commit()
            except Exception:
                connection.rollback()
                raise


def run_worker(settings: Settings | None = None) -> None:
    active_settings = settings or Settings.from_environment()
    database = Database(active_settings.database_path)
    database.initialize(applied_at=isoformat(utc_now()))
    queue = WorkflowQueue(
        database,
        lease_seconds=active_settings.job_lease_seconds,
    )
    stop = Event()
    worker_id = f"worker-{uuid4()}"

    def request_stop(_: int, __: object) -> None:
        stop.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)

    while not stop.is_set():
        job = queue.claim_next(worker_id=worker_id)
        if job is None:
            stop.wait(active_settings.worker_poll_seconds)
            continue
        try:
            if active_settings.worker_processing_delay_seconds > 0:
                interrupted = stop.wait(active_settings.worker_processing_delay_seconds)
                if interrupted:
                    return
            if job.kind != "collect_evidence":
                raise ValueError(f"unsupported job kind: {job.kind}")
            queue.complete(job)
        except Exception as error:
            queue.fail(job, error=str(error))


def main() -> None:
    run_worker()
