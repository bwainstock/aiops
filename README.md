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

## Checks

```bash
make sync
make check
```
