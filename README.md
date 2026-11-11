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

## Checks

```bash
make sync
make check
make observability-check
```
