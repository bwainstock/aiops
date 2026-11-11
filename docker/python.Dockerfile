ARG PYTHON_VERSION=3.13
ARG UV_VERSION=0.13.7
ARG WORKSPACE_PACKAGE=aiops-demo-api
ARG PACKAGE_PATH=apps/demo_api

FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv
FROM python:${PYTHON_VERSION}-slim

ARG WORKSPACE_PACKAGE
ARG PACKAGE_PATH

COPY --from=uv /uv /uvx /bin/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/workspace/.venv/bin:${PATH}"

WORKDIR /workspace

COPY pyproject.toml uv.lock ./
COPY apps/aiops/pyproject.toml apps/aiops/pyproject.toml
COPY apps/alert_sink/pyproject.toml apps/alert_sink/pyproject.toml
COPY apps/demo_api/pyproject.toml apps/demo_api/pyproject.toml
COPY apps/loadgen/pyproject.toml apps/loadgen/pyproject.toml
COPY packages/ops_mcp/pyproject.toml packages/ops_mcp/pyproject.toml
COPY packages/shared/pyproject.toml packages/shared/pyproject.toml
COPY ${PACKAGE_PATH}/src ${PACKAGE_PATH}/src

RUN uv sync --frozen --no-dev --no-editable --package ${WORKSPACE_PACKAGE}

RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --home-dir /nonexistent app

USER app

EXPOSE 8000
STOPSIGNAL SIGTERM

CMD ["uvicorn", "aiops_demo_api.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-server-header", "--no-access-log"]
