from typing import Literal
from uuid import uuid4

from aiops_core.approval_models import ApprovalDecision, CandidateView
from aiops_core.database import Database
from aiops_core.intake import canonical_json, isoformat, utc_now
from aiops_core.remediation_models import RemediationCandidate
from aiops_core.validation import validation_report_digest
from aiops_core.validation_models import ValidationReport


class CandidateRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    def store_candidate(self, candidate: RemediationCandidate) -> None:
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO remediation_candidates(
                    id, incident_id, base_revision, hypothesis_digest,
                    patch_digest, status, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    incident_id = excluded.incident_id,
                    base_revision = excluded.base_revision,
                    hypothesis_digest = excluded.hypothesis_digest,
                    patch_digest = excluded.patch_digest,
                    status = excluded.status,
                    payload_json = excluded.payload_json
                """,
                (
                    candidate.id,
                    candidate.incident_id,
                    candidate.base_revision,
                    candidate.hypothesis_digest,
                    candidate.patch_digest,
                    candidate.status,
                    canonical_json(candidate.model_dump(mode="json")),
                    isoformat(utc_now()),
                ),
            )

    def store_validation(self, report: ValidationReport) -> None:
        if report.digest != validation_report_digest(report):
            raise ValueError("validation report digest does not match")
        candidate = self.get_candidate(report.candidate_id)
        if candidate is None:
            raise ValueError("candidate does not exist")
        if report.patch_digest != candidate.patch_digest:
            raise ValueError("validation patch digest does not match candidate")
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO validation_reports(
                    candidate_id, validation_digest, status, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    report.candidate_id,
                    report.digest,
                    report.status,
                    canonical_json(report.model_dump(mode="json")),
                    isoformat(utc_now()),
                ),
            )

    def record_decision(
        self,
        *,
        candidate_id: str,
        candidate_digest: str,
        validation_digest: str,
        decision: Literal["approve", "reject"],
        reason: str,
        merge_commit: str | None,
    ) -> ApprovalDecision:
        decision_id = str(uuid4())
        created_at = utc_now()
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO approval_decisions(
                    id, candidate_id, candidate_digest, validation_digest,
                    decision, reason, merge_commit, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    decision_id,
                    candidate_id,
                    candidate_digest,
                    validation_digest,
                    decision,
                    reason,
                    merge_commit,
                    isoformat(created_at),
                ),
            )
        return ApprovalDecision(
            id=decision_id,
            candidate_id=candidate_id,
            candidate_digest=candidate_digest,
            validation_digest=validation_digest,
            decision=decision,
            reason=reason,
            merge_commit=merge_commit,
            created_at=created_at,
        )

    def get_candidate(self, candidate_id: str) -> RemediationCandidate | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM remediation_candidates WHERE id = ?",
                (candidate_id,),
            ).fetchone()
        return (
            RemediationCandidate.model_validate_json(row["payload_json"])
            if row is not None
            else None
        )

    def get_validation(self, candidate_id: str) -> ValidationReport | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM validation_reports WHERE candidate_id = ?",
                (candidate_id,),
            ).fetchone()
        return (
            ValidationReport.model_validate_json(row["payload_json"]) if row is not None else None
        )

    def latest_decision(self, candidate_id: str) -> ApprovalDecision | None:
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM approval_decisions
                WHERE candidate_id = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (candidate_id,),
            ).fetchone()
        return ApprovalDecision.model_validate(dict(row)) if row is not None else None

    def list_views(self) -> list[CandidateView]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT id
                FROM remediation_candidates
                ORDER BY created_at DESC
                """
            ).fetchall()
        views: list[CandidateView] = []
        for row in rows:
            candidate = self.get_candidate(row["id"])
            if candidate is None:
                continue
            views.append(
                CandidateView(
                    candidate=candidate,
                    validation=self.get_validation(row["id"]),
                    decision=self.latest_decision(row["id"]),
                )
            )
        return views
