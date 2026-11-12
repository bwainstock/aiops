# Local AIOps Operations Guide

This guide starts the complete local stack, verifies each interface, runs the
fault scenarios, and explains recovery and cleanup. All application
infrastructure runs locally; GitHub Copilot model calls and optional GitHub MCP
queries are the intended remote dependencies.

## Prerequisites

- Docker Desktop with Compose v2
- Git
- GitHub CLI authenticated with an account that can use Copilot
- GitHub Copilot CLI authenticated through the same account
- `uv`
- `jq` and `rg`

Verify the host:

```bash
docker info
git --version
gh auth status --active
copilot --version
uv --version
jq --version
rg --version
```

## Clean checkout setup

```bash
git clone https://github.com/bwainstock/aiops.git
cd aiops
uv sync --all-packages --locked
make check
make observability-check
```

`make check` runs formatting, linting, strict type checking, the test suite, and
Compose configuration validation. `make observability-check` validates the
Prometheus rules/configuration, Alertmanager, Loki, Alloy, Tempo, and the
provisioned Grafana dashboard.

## Start the stack

```bash
docker compose up -d --build
docker compose ps
```

Wait until `demo-api`, `alert-sink`, and `aiops-api` report healthy.

Local interfaces:

| Interface | URL |
|---|---|
| Demo API | <http://127.0.0.1:8000/healthz> |
| AIOps dashboard | <http://127.0.0.1:8081/dashboard> |
| AIOps API docs | <http://127.0.0.1:8081/docs> |
| Grafana | <http://127.0.0.1:3000> |
| Prometheus | <http://127.0.0.1:9090> |
| Alertmanager | <http://127.0.0.1:9093> |
| Loki | <http://127.0.0.1:3100/ready> |
| Tempo | <http://127.0.0.1:3200/ready> |
| Alloy | <http://127.0.0.1:12345> |
| Investigation MCP | <http://127.0.0.1:8082/mcp> |

Grafana permits anonymous dashboard viewing. Grafana Explore requires login
with the local default credentials `admin` / `admin` unless overridden with
`GRAFANA_ADMIN_PASSWORD`.

## Verify telemetry

```bash
docker compose --profile load run --rm loadgen

curl -s http://127.0.0.1:8000/metrics |
  rg '^demo_(http_requests_total|http_request_duration_seconds_count|build_info)'

docker compose logs --no-log-prefix demo-api |
  rg '^\{' |
  tail -5 |
  jq .
```

To correlate logs and traces:

```bash
curl -s -H 'X-Request-ID: operations-trace-check' \
  http://127.0.0.1:8000/api/items >/dev/null

sleep 8

trace_id=$(
  curl -sG \
    --data-urlencode 'query={service="demo-api"} | json | request_id="operations-trace-check"' \
    --data-urlencode 'limit=10' \
    http://127.0.0.1:3100/loki/api/v1/query_range |
  jq -r '.data.result[0].stream.trace_id'
)

curl -s "http://127.0.0.1:3200/api/traces/$trace_id" |
  jq '.batches | length'
```

## Verify alerting and durable intake

Controlled triggers:

```bash
make clear-alert-events
make trigger-error
make trigger-latency
make trigger-down
make clear-down
```

Inspect durable incidents and jobs:

```bash
curl -s http://127.0.0.1:8081/api/v1/incidents | jq .
curl -s http://127.0.0.1:8081/api/v1/jobs | jq .
```

Prometheus alerts use short test windows. Error and latency alerts normally
become firing during their load runs and resolve roughly 30–45 seconds after
the triggering traffic stops. `TargetDown` becomes firing after the demo
container has been unavailable for ten seconds.

## Verify investigation and Copilot

```bash
make copilot-preflight
make copilot-smoke
```

The preflight verifies authentication and model discovery. The smoke test calls
the local Prometheus MCP tool through the official Copilot SDK, validates a
typed response, records tool audit events, and resumes the same persisted
session.

Run triage for a durable incident:

```bash
make triage INCIDENT_ID=<incident-id>
```

Triage is fail-closed. A result of `more_evidence` is successful behavior when
the symptom is proven but causality is not.

## Verify remediation, validation, and approval

Create an isolated candidate:

```bash
make remediation-smoke
```

The output contains the candidate record path. Validate it:

```bash
make validation-smoke \
  CANDIDATE_RECORD=data/remediation-candidates/<candidate-id>.json
```

Publish it to the dashboard:

```bash
make publish-candidate \
  CANDIDATE_RECORD=data/remediation-candidates/<candidate-id>.json \
  VALIDATION_RECORD=data/validation-reports/<candidate-id>.json
```

Approval is accepted only when:

- the candidate and validation digests exactly match;
- every deterministic validation gate passed;
- the independent review approved;
- the candidate base is still the current `main` commit;
- the main working tree is clean;
- the candidate worktree patch still matches its digest.

Rejection records a reason without changing `main`. Approval creates the
candidate commit and merges its branch locally with `--no-ff`.

## End-to-end scenarios

Repeatable, network-free fake-agent scenarios:

```bash
make e2e-fake
```

Opt-in live scenarios:

```bash
make e2e-live-error
make e2e-live-latency
```

The live error scenario invokes Copilot triage. The live latency scenario
verifies the alert and durable incident lifecycle; adaptive latency evidence
gathering is deferred by
[ADR-0001](./adr/0001-adaptive-evidence-gathering.md).

## Restart and recovery

Restart durable intake without deleting state:

```bash
docker compose restart aiops-api aiops-worker
```

SQLite lives in the `aiops-data` named volume. Leased jobs become reclaimable
after their lease expires. Recovered jobs retain their attempt count and append
`job.lease_expired` to the audit trail.

Restart the observability stack:

```bash
docker compose restart prometheus loki tempo alloy grafana
```

Named volumes retain local data across container restarts and recreation.

## Cleanup

Stop containers while retaining data:

```bash
docker compose down
```

Delete all local Compose volumes and their stored incidents, telemetry, and
dashboard state:

```bash
docker compose down --volumes --remove-orphans
```

The second command is intentionally destructive. It does not delete repository
files or Git worktrees.

List remediation worktrees:

```bash
git worktree list
```

Remove one named candidate worktree only after it is no longer needed:

```bash
git worktree remove --force worktrees/<candidate-id>
git branch -D aiops/<incident>/<candidate-prefix>
```

## Add a new alert

1. Add the Prometheus expression and labels to
   `observability/prometheus/rules/demo-api.yml`.
2. Include `service`, `severity`, and a canonical `symptom` label.
3. Add a controlled trigger and clear mechanism.
4. Run `make observability-check`.
5. Verify pending, firing, inactive, firing webhook, and resolved webhook
   transitions.
6. Add a focused end-to-end scenario.

## Add a new investigation tool

1. Implement a bounded adapter under `packages/ops_mcp`.
2. Enforce input, time-window, count, byte, path, and timeout limits.
3. Redact secrets before returning results.
4. Expose a typed MCP tool in `aiops_ops_mcp.server`.
5. Add the exact tool name to `config/tool-profiles.json`; never use a wildcard
   for the triage profile.
6. Test the adapter directly, through live MCP, and through Copilot permission
   hooks.

## Known limitations

- The current evidence collector contains scenario-specific error queries.
  Bounded adaptive LLM-directed investigation is documented as a proposed
  design in ADR-0001.
- The current durable workflow is a local SQLite lease/job state machine.
  Temporal and Hatchet are evaluated separately before adopting either.
- GitHub remains read-only in the MVP.
- Human approval is local and unauthenticated; ports bind to localhost by
  default.
