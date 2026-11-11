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

## Checks

```bash
make sync
make check
```
