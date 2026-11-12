import hashlib
import json
from uuid import uuid4

from aiops_core.agent_models import AgentAuditEvent, AgentTask
from aiops_core.database import Database
from aiops_core.intake import canonical_json, isoformat, utc_now


class AgentAuditStore:
    def __init__(self, database: Database) -> None:
        self.database = database

    def start(
        self,
        *,
        task: AgentTask,
        provider: str,
        session_id: str,
    ) -> str:
        run_id = str(uuid4())
        prompt_digest = hashlib.sha256(task.prompt.encode()).hexdigest()
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO agent_runs(
                    id, incident_id, profile, provider, session_id, status,
                    prompt_digest, created_at
                ) VALUES (?, ?, ?, ?, ?, 'running', ?, ?)
                """,
                (
                    run_id,
                    task.incident_id,
                    task.profile,
                    provider,
                    session_id,
                    prompt_digest,
                    isoformat(utc_now()),
                ),
            )
        return run_id

    def append(self, run_id: str, event: AgentAuditEvent) -> None:
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO agent_events(
                    run_id, event_type, created_at, payload_json
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    run_id,
                    event.event_type,
                    isoformat(utc_now()),
                    canonical_json(event.payload),
                ),
            )

    def complete(self, run_id: str, output: BaseException | dict[str, object]) -> None:
        completed_at = isoformat(utc_now())
        with self.database.connect() as connection:
            if isinstance(output, BaseException):
                connection.execute(
                    """
                    UPDATE agent_runs
                    SET status = 'failed', error = ?, completed_at = ?
                    WHERE id = ?
                    """,
                    (str(output), completed_at, run_id),
                )
            else:
                connection.execute(
                    """
                    UPDATE agent_runs
                    SET status = 'completed', output_json = ?, completed_at = ?
                    WHERE id = ?
                    """,
                    (canonical_json(output), completed_at, run_id),
                )

    def events(self, run_id: str) -> list[AgentAuditEvent]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT event_type, payload_json
                FROM agent_events
                WHERE run_id = ?
                ORDER BY id
                """,
                (run_id,),
            ).fetchall()
        return [
            AgentAuditEvent(
                event_type=row["event_type"],
                payload=json.loads(row["payload_json"]),
            )
            for row in rows
        ]
