# Temporal versus Hatchet for the local AIOps workflow

## Recommendation

Keep the current SQLite lease/job workflow for the MVP. It already provides the
capabilities the implemented workflow presently needs: transactional alert/job
creation, idempotent event handling, bounded retries, lease recovery, audit
history, and local visibility through the AIOps API and dashboard.

If the workflow becomes difficult to maintain because it gains long-running
multi-step coordination, durable human-approval waits, or many independently
retryable activities, run a **Hatchet-first spike**. Hatchet is the closer
incremental fit for the current Python task/job shape and has a documented
durable event-wait model with event lookback. Use Temporal as the comparison
spike when replay guarantees, workflow-code versioning, or larger operational
scale become primary requirements.

This recommendation does not select Hatchet for production. It selects the
first experiment to run after the SQLite module demonstrates a concrete
limitation.

## Requirements demonstrated by this repository

The current system needs:

- idempotent alert and deployment ingestion;
- one durable incident per alert fingerprint;
- atomic event and workflow-job creation;
- worker leases that recover after process death;
- bounded retries and retry exhaustion;
- human approval bound to exact candidate and validation digests;
- duplicate, early, stale, and rejected decisions to remain safe;
- a complete application-level audit trail;
- local Docker Compose operation with low setup overhead.

The current worker processes one job kind before handing control to explicit
commands for triage, remediation, validation, and approval. The system has not
yet demonstrated a need for a general workflow engine.

## Comparison

| Area | Temporal | Hatchet | Current SQLite module |
|---|---|---|---|
| Durable execution | Deterministic workflow replay from event history; side effects belong in Activities | Durable tasks replay around checkpoints; side effects are normally delegated to child tasks | Durable rows, leases, retries, and explicit state transitions |
| Human approval | Workflow-addressed Signals and Updates | Durable event waits with payload filters and lookback windows | Digest-bound HTTP decision plus persisted approval row |
| Retry semantics | Activities retry by default; policy and timeouts must be bounded explicitly | Task retries, capped backoff, scheduling timeout, and execution timeout | `max_attempts` counts total attempts; exponential capped backoff |
| Versioning | Explicit worker versioning and replay-compatible workflow patching | Workers register workflow versions; equivalent long-running Python replay guarantees were not verified | Ordinary application migrations and explicit state handling |
| Visibility | Web UI exposes workflow histories and event details | Web UI, workflow logs, and self-hosted Prometheus metrics | AIOps API, audit rows, Grafana, and local dashboard |
| Local development | Development server with UI and optional local-file persistence; not production-ready | Hatchet Lite uses PostgreSQL; embedded local mode downloads a sidecar and embedded PostgreSQL | SQLite named volume; no additional control-plane service |
| Python SDK | Official `temporalio` SDK | Official `hatchet-sdk` | Standard library SQLite and project modules |
| Migration distance | Larger conceptual shift to workflows/activities and deterministic replay | Closer to task/event terminology already used in the project | Already implemented and verified |

## Durable execution and replay

Temporal reconstructs workflow state by replaying deterministic workflow code
against persisted event history. Network calls and other non-deterministic side
effects belong in Activities. Temporal also emphasizes idempotency for Activity
side effects because Activities can be retried.

Sources:

- [Temporal Python durable execution and replay](https://learn.temporal.io/tutorials/python/background-check/durable-execution/)
- [Temporal Python error handling and Activity idempotency](https://docs.temporal.io/develop/python/best-practices/error-handling)

Hatchet durable tasks persist progress around durable checkpoints. Its
documentation recommends using child tasks for side effects and describes how
durable execution resumes after worker interruption. Retried side effects still
need application-level idempotency.

Sources:

- [Hatchet durable tasks](https://docs.hatchet.run/v1/durable-tasks)
- [Hatchet retry policies](https://docs.hatchet.run/v1/retry-policies)

Neither engine replaces the repository’s existing idempotency requirements for
alert ingestion, candidate publishing, Git commits, or merges.

## Human approval and external events

Temporal workflows receive asynchronous input through Signals and synchronous
or validated interaction through Updates. These messages are addressed to a
specific workflow execution and become part of its durable history.

Source:

- [Temporal Python workflow message passing](https://docs.temporal.io/develop/python/workflows/message-passing)

Hatchet supports durable event waits with payload matching and scoped lookback
windows. Lookback is relevant to human approval because an approval can arrive
before the workflow reaches the wait step; the workflow can still find the
matching earlier event.

Source:

- [Hatchet durable event waits](https://docs.hatchet.run/v1/durable-event-waits)

Whichever engine is tested must preserve the current approval rules:

- candidate and validation digests must match exactly;
- stale candidate bases must be rejected;
- rejection must never alter `main`;
- approval may merge only the validated worktree contents;
- duplicate or early decisions must be idempotent and auditable.

## Retries and timeouts

Temporal Activities have default retry behavior. Production workflows should
configure maximum attempts or non-retryable errors where unlimited retries are
not appropriate, and should select Activity timeouts and heartbeats
deliberately.

Sources:

- [Temporal retry policies](https://docs.temporal.io/encyclopedia/retry-policies)
- [Temporal Python Activity timeouts](https://docs.temporal.io/develop/python/activities/timeouts)

Hatchet exposes retries with backoff plus scheduling and execution timeouts.
Hatchet’s `retries` value counts retries after the initial attempt. The current
SQLite workflow’s `max_attempts` counts all attempts, so migration must translate
this semantic difference explicitly.

Sources:

- [Hatchet retry policies](https://docs.hatchet.run/v1/retry-policies)
- [Hatchet timeouts](https://docs.hatchet.run/v1/timeouts)

## Workflow versioning

Temporal explicitly documents worker versioning and replay-compatible workflow
patching. This is a strong reason to prefer Temporal if long-lived workflows
must survive incompatible code deployments with well-defined replay behavior.

Source:

- [Temporal Python workflow versioning](https://docs.temporal.io/develop/python/workflows/versioning)

Hatchet workers register workflow versions. This investigation did not verify a
first-party guarantee equivalent to Temporal’s replay/versioning model for
already-running Python durable workflows. A Hatchet spike must deploy an
incompatible workflow change while a run is waiting, then verify how that run
resumes before relying on version pinning.

Source:

- [Hatchet Python worker implementation](https://github.com/hatchet-dev/hatchet/blob/main/sdks/python/hatchet_sdk/worker/worker.py#L349-L353)

## Local and operational overhead

Hatchet Lite’s documented self-hosted Compose setup adds Hatchet Lite and
PostgreSQL. Hatchet’s embedded mode is aimed at local testing and starts a
downloaded sidecar plus embedded PostgreSQL; it does not replace SQLite with an
in-process library.

Sources:

- [Hatchet Lite self-hosting](https://docs.hatchet.run/self-hosting/hatchet-lite)
- [Hatchet embedded local mode](https://docs.hatchet.run/v1/embedded)

Temporal’s development server includes a UI and can persist data to a local
file, but its documentation states that it is not production-ready. Temporal
also documents fuller self-hosted deployments.

Sources:

- [Temporal CLI development server](https://docs.temporal.io/cli/command-reference/server)
- [Temporal self-hosted deployment guide](https://docs.temporal.io/self-hosted-guide/deployment)

No resource measurements were taken. A spike must measure startup time, memory,
CPU, image size, and clean-reset behavior on this project’s Docker Desktop
environment.

## UI, logging, metrics, and licensing

Both systems provide local visibility. Hatchet documents workflow logs and
self-hosted Prometheus metrics. Temporal’s UI exposes workflow event history,
which is central to replay debugging.

Sources:

- [Hatchet workflow logging](https://docs.hatchet.run/v1/logging)
- [Hatchet self-hosted Prometheus metrics](https://docs.hatchet.run/self-hosting/prometheus-metrics)
- [Temporal event history walkthrough](https://learn.temporal.io/tutorials/python/background-check/durable-execution/)

The core Hatchet and Temporal server repositories use the MIT license.

Sources:

- [Hatchet license](https://github.com/hatchet-dev/hatchet/blob/main/LICENSE)
- [Temporal server license](https://github.com/temporalio/temporal/blob/main/LICENSE)

Both publish official Python SDKs compatible with the repository’s Python 3.11
minimum. At the time of this research, PyPI reported `temporalio` 1.36.0 and
`hatchet-sdk` 1.45.1. A spike should pin the evaluated version and verify each
required feature rather than relying on latest-version behavior.

Sources:

- [Temporal Python package metadata](https://pypi.org/pypi/temporalio/json)
- [Hatchet Python package metadata](https://pypi.org/pypi/hatchet-sdk/json)

## Migration boundary

Keep SQLite as the incident, audit, evidence, candidate, validation, and
approval source of truth during any orchestration spike. Move only workflow
ownership behind an idempotent dispatch/outbox seam:

1. Commit the application event and outbox command in one SQLite transaction.
2. Dispatch the command to the candidate workflow engine with a stable
   idempotency key.
3. Record the external workflow/run ID.
4. Project engine events back into the existing application audit model.
5. Keep Git candidate creation, validation digests, and merge policy in the
   application modules.

The following scenarios must pass before migration:

- duplicate Alertmanager delivery;
- worker death before and after a side effect;
- retry exhaustion;
- approval arriving before the workflow begins waiting;
- duplicate approval;
- stale candidate or validation digest;
- rejection with no repository mutation;
- Git commit succeeding but workflow acknowledgement failing;
- incompatible worker deployment while a workflow waits;
- full local reset and recovery.

## First spike acceptance criteria

Spike Hatchet first only when at least one concrete SQLite limitation has been
recorded. The spike should:

- implement one alert-to-approval workflow;
- preserve the existing typed event and audit records;
- test early approval using event lookback;
- reproduce worker death and retry exhaustion;
- deploy an incompatible workflow change during a wait;
- measure local operational overhead;
- leave remediation and merge policy in the current application modules.

Run the equivalent Temporal spike if Hatchet cannot provide sufficiently clear
versioning/replay behavior, or if deterministic long-lived workflow replay
becomes the dominant requirement.
