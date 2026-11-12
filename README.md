# Local AIOps Lab

A local, containerized AIOps learning system. The repository is being built as a
sequence of verified vertical stages.

## Demo API

Stage 2 provides a deterministic FastAPI workload at:

- `GET /healthz`
- `GET /api/items?offset=0&limit=10`

Run it with:

```bash
docker compose up --build demo-api
```

The service binds to `127.0.0.1:8000` by default. `BUILD_VERSION`, `COMMIT_SHA`,
and `DEMO_API_PORT` can be overridden in the environment.

## Source telemetry

The demo API exposes Prometheus metrics at `GET /metrics` and emits one JSON log
record per request. Request IDs are accepted through `X-Request-ID`, generated
when absent or invalid, returned in the response, and included in request logs.

Generate a deterministic 100-request workload with:

```bash
docker compose --profile load run --rm loadgen
```

Inspect the emitted telemetry directly:

```bash
curl -s http://127.0.0.1:8000/metrics
docker compose logs --no-log-prefix demo-api | grep '^{'
```

## Local observability stack

Start the provisioned stack:

```bash
docker compose up -d demo-api prometheus loki tempo alloy grafana
```

Local interfaces:

- Grafana: <http://127.0.0.1:3000>
- Prometheus: <http://127.0.0.1:9090>
- Loki: <http://127.0.0.1:3100>
- Tempo: <http://127.0.0.1:3200>
- Alloy: <http://127.0.0.1:12345>

Prometheus scrapes metrics directly. Alloy discovers the demo container and
ships its logs to Loki. The demo API exports OpenTelemetry traces to Alloy,
which batches and forwards them to Tempo. Request logs include `trace_id` and
`span_id`, and the provisioned Tempo datasource links traces back to Loki logs.

Open the provisioned **AIOps Lab / AIOps Demo Service** dashboard in Grafana for
service health, request rate, p95 latency, errors, and logs. Use Grafana Explore
with the Tempo datasource to search:

```traceql
{ resource.service.name = "demo-api" }
```

## Alert pipeline

Prometheus evaluates three symptom alerts:

- `HighErrorRate`
- `HighLatencyP95`
- `TargetDown`

Alertmanager routes firing and resolved notifications to a queryable local
capture sink. Useful interfaces:

- Prometheus alerts: <http://127.0.0.1:9090/alerts>
- Alertmanager: <http://127.0.0.1:9093>
- Captured events: <http://127.0.0.1:8090/api/v1/events>

Controlled triggers:

```bash
make clear-alert-events
make trigger-error
make trigger-latency
make trigger-down
make clear-down
```

Error and latency traffic is activated only by the internal `X-Demo-Fault`
header used by the load generator. Normal traffic remains healthy.

## Durable AIOps intake

Alertmanager now posts to the AIOps API at
`POST /api/v1/events/alertmanager`. The API normalizes each alert into one
incident per fingerprint and transactionally appends its workflow job. A
separate worker leases jobs from the same SQLite database, retries failures with
bounded backoff, and recovers expired leases after restarts.

Useful endpoints:

- API health: <http://127.0.0.1:8081/healthz>
- Incidents: <http://127.0.0.1:8081/api/v1/incidents>
- Workflow jobs: <http://127.0.0.1:8081/api/v1/jobs>
- Deployment intake: `POST http://127.0.0.1:8081/api/v1/events/deployments`

SQLite state is stored in the `aiops-data` named volume. The `alert-sink`
module remains available as a test utility on port 8090, but it is no longer the
Alertmanager receiver.

## Read-only investigation MCP

The local MCP server is available at <http://127.0.0.1:8082/mcp>. It exposes
bounded read-only tools for:

- Prometheus instant/range queries;
- Loki range queries;
- local Git status, history, show, diff, and blame;
- Compose-project container inventory, safe inspect summaries, and bounded logs.

Docker access goes through a separate GET-only socket proxy. The MCP container
never receives the Docker socket, and Docker mutation endpoints remain disabled.
Query windows, result counts, output bytes, repository paths, revisions, and log
sizes are validated before execution; common credential patterns are redacted.

The triage allowlist is pinned in `config/tool-profiles.json`. GitHub queries
remain separate from the local MCP and use Copilot's built-in read-only GitHub
MCP endpoint; write access is explicitly disabled in the profile.

## Copilot SDK agent runner

The AIOps package uses the official Python `github-copilot-sdk` behind a small
agent-runner interface. The production adapter:

- verifies Copilot authentication before opening a session;
- enables only MCP-class tools, disables built-in MCP servers, and configures
  the local MCP with its explicit 11-tool allowlist;
- independently enforces the same allowlist in permission callbacks and
  pre-tool hooks;
- validates each response against a Pydantic response schema;
- persists sessions, tool decisions/results, and structured outputs;
- redacts and bounds audit payloads;
- applies the SDK runtime's minimum 30-AI-credit session limit.

Most tests use the deterministic fake adapter and do not call Copilot. The
opt-in live checks use the signed-in local Copilot CLI account:

```bash
make copilot-preflight
make copilot-smoke
```

The AIOps image predownloads the SDK's pinned runtime. Credentials are not baked
into the image; future container execution must receive a supported runtime
token or an isolated writable Copilot state volume.

## Checks

```bash
make sync
make check
make observability-check
```
