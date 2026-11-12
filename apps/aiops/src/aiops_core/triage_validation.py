import hashlib
import json

from aiops_core.triage_models import (
    EvidenceBundle,
    EvidenceItem,
    TriageHypothesis,
    TriageReview,
)


def canonical_digest(value: object) -> str:
    encoded = json.dumps(
        value,
        separators=(",", ":"),
        sort_keys=True,
        default=str,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def evidence_item_digest(item: EvidenceItem) -> str:
    return canonical_digest(item.model_dump(mode="json", exclude={"digest"}))


def evidence_bundle_digest(bundle: EvidenceBundle) -> str:
    return canonical_digest(bundle.model_dump(mode="json", exclude={"digest"}))


def validate_hypothesis(
    bundle: EvidenceBundle,
    hypothesis: TriageHypothesis,
) -> list[str]:
    errors: list[str] = []
    evidence_by_id = {item.id: item for item in bundle.items}
    if len(evidence_by_id) != len(bundle.items):
        errors.append("evidence IDs must be unique")
    for item in bundle.items:
        if item.digest != evidence_item_digest(item):
            errors.append(f"evidence digest mismatch: {item.id}")
    if bundle.digest != evidence_bundle_digest(bundle):
        errors.append("evidence bundle digest mismatch")
    if bundle.window_end <= bundle.window_start:
        errors.append("evidence window is invalid")

    ranks = [cause.rank for cause in hypothesis.suspected_causes]
    if ranks != list(range(1, len(ranks) + 1)):
        errors.append("suspected cause ranks must be contiguous and ordered")

    for cause in hypothesis.suspected_causes:
        missing = [item_id for item_id in cause.evidence_ids if item_id not in evidence_by_id]
        if missing:
            errors.append(f"cause {cause.rank} cites unknown evidence: {', '.join(missing)}")
    unknown_contradictions = [
        item_id
        for item_id in hypothesis.contradictory_evidence_ids
        if item_id not in evidence_by_id
    ]
    if unknown_contradictions:
        errors.append(
            "contradictory evidence IDs are unknown: " + ", ".join(unknown_contradictions)
        )

    alert_items = [
        item
        for item in bundle.items
        if item.source == "alertmanager" and item.kind == "alert.firing"
    ]
    if not alert_items:
        errors.append("the firing alert is missing from evidence")

    error_ratio_items = [
        item for item in bundle.items if item.source == "prometheus" and item.kind == "error_ratio"
    ]
    reproduced = any(_metric_value(item) > 0.25 for item in error_ratio_items)
    if not reproduced:
        errors.append("the error-rate symptom was not reproduced in Prometheus evidence")

    alert_start = min((item.observed_at for item in alert_items), default=None)
    deployment_items = [item for item in bundle.items if item.source == "deployment"]
    if alert_start is not None:
        for deployment in deployment_items:
            if deployment.observed_at > alert_start:
                errors.append(f"deployment evidence occurs after the alert: {deployment.id}")
    return errors


def validate_review(
    bundle: EvidenceBundle,
    hypothesis_errors: list[str],
    review: TriageReview,
) -> list[str]:
    errors: list[str] = []
    evidence_ids = {item.id for item in bundle.items}
    unknown = [item_id for item_id in review.evidence_ids if item_id not in evidence_ids]
    if unknown:
        errors.append("review cites unknown evidence: " + ", ".join(unknown))
    if hypothesis_errors and review.verdict == "approve":
        errors.append("review cannot approve an invalid hypothesis")
    if review.verdict == "approve" and review.issues:
        errors.append("approved review must not contain unresolved issues")
    return errors


def _metric_value(item: EvidenceItem) -> float:
    value = item.content.get("value")
    if isinstance(value, (int, float)):
        return float(value)
    return 0
