---
status: accepted
---

# Retain SQLite workflow ownership and spike Hatchet first when needed

The completed MVP keeps its SQLite lease/job workflow because it already satisfies the demonstrated durability, retry, audit, and local-operation requirements without another control-plane service. If long-running coordination or durable approval waits make the module materially difficult to maintain, the first orchestration spike will use Hatchet because its task/event model and durable event lookback are the closer incremental fit; Temporal remains the comparison when replay guarantees and workflow versioning become primary. Any spike must keep incidents, evidence, approvals, candidate policy, and merge safeguards in the application and migrate workflow ownership only through an idempotent outbox seam.
