ARG PYTHON_VERSION=3.13
FROM python:${PYTHON_VERSION}-slim

ARG UV_VERSION=0.13.7
COPY --from=ghcr.io/astral-sh/uv:${UV_VERSION} /uv /uvx /bin/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/workspace/.venv/bin:${PATH}"

WORKDIR /workspace

COPY pyproject.toml uv.lock ./
COPY apps/aiops/pyproject.toml apps/aiops/pyproject.toml
COPY apps/demo_api/pyproject.toml apps/demo_api/pyproject.toml
COPY apps/loadgen/pyproject.toml apps/loadgen/pyproject.toml
COPY packages/ops_mcp/pyproject.toml packages/ops_mcp/pyproject.toml
COPY packages/shared/pyproject.toml packages/shared/pyproject.toml
COPY apps/demo_api/src apps/demo_api/src

RUN uv sync --frozen --no-dev --no-editable --package aiops-demo-api

RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --home-dir /nonexistent app

USER app

EXPOSE 8000
STOPSIGNAL SIGTERM

CMD ["uvicorn", "aiops_demo_api.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-server-header"]
