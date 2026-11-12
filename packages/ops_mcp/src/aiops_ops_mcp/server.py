import os
from collections.abc import Callable
from pathlib import Path
from typing import Literal, TypeVar

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from aiops_ops_mcp.docker_adapter import DockerAdapter
from aiops_ops_mcp.git_adapter import GitAdapter
from aiops_ops_mcp.http_client import BoundedHttpClient
from aiops_ops_mcp.loki import LokiAdapter
from aiops_ops_mcp.prometheus import PrometheusAdapter

PROMETHEUS_URL = os.environ.get("PROMETHEUS_URL", "http://prometheus:9090")
LOKI_URL = os.environ.get("LOKI_URL", "http://loki:3100")
DOCKER_PROXY_URL = os.environ.get("DOCKER_PROXY_URL", "http://docker-proxy:2375")
REPOSITORY_PATH = Path(os.environ.get("OPS_REPOSITORY_PATH", "/workspace")).resolve()
MCP_HOST = os.environ.get("OPS_MCP_HOST", "0.0.0.0")
MCP_PORT = int(os.environ.get("OPS_MCP_PORT", "8082"))
ResultT = TypeVar("ResultT")

prometheus = PrometheusAdapter(BoundedHttpClient(PROMETHEUS_URL))
loki = LokiAdapter(BoundedHttpClient(LOKI_URL))
git = GitAdapter(REPOSITORY_PATH)
docker = DockerAdapter(BoundedHttpClient(DOCKER_PROXY_URL))

mcp = MCPServer(
    "local-ops",
    description="Bounded read-only operational investigation tools.",
    instructions=(
        "All tools are read-only. Query narrow time windows and cite returned evidence. "
        "Do not infer success from an empty result."
    ),
)


@mcp.tool()
def prometheus_query(query: str, time: str | None = None) -> dict[str, object]:
    """Run one bounded Prometheus instant query."""
    return _invoke(lambda: prometheus.query(query, time=time))


@mcp.tool()
def prometheus_query_range(
    query: str,
    start: str,
    end: str,
    step_seconds: float,
) -> dict[str, object]:
    """Run a Prometheus range query limited to one hour and 2,000 points."""
    return _invoke(
        lambda: prometheus.query_range(
            query,
            start=start,
            end=end,
            step_seconds=step_seconds,
        )
    )


@mcp.tool()
def loki_query_range(
    query: str,
    start: str,
    end: str,
    limit: int = 100,
    direction: Literal["forward", "backward"] = "backward",
) -> dict[str, object]:
    """Query at most 200 Loki log lines over at most one hour."""
    return _invoke(
        lambda: loki.query_range(
            query,
            start=start,
            end=end,
            limit=limit,
            direction=direction,
        )
    )


@mcp.tool()
def git_status() -> dict[str, object]:
    """Return read-only Git branch and working-tree status."""
    return _invoke(git.status)


@mcp.tool()
def git_log(
    revision: str = "HEAD",
    limit: int = 20,
    path: str | None = None,
) -> dict[str, object]:
    """Return bounded commit history for an allowlisted repository path."""
    return _invoke(lambda: git.log(revision=revision, limit=limit, path=path))


@mcp.tool()
def git_show(revision: str = "HEAD", path: str | None = None) -> dict[str, object]:
    """Show one bounded commit or file revision without external diff tools."""
    return _invoke(lambda: git.show(revision, path=path))


@mcp.tool()
def git_diff(
    base: str,
    head: str = "HEAD",
    path: str | None = None,
) -> dict[str, object]:
    """Return a bounded three-dot Git diff."""
    return _invoke(lambda: git.diff(base=base, head=head, path=path))


@mcp.tool()
def git_blame(
    path: str,
    start_line: int = 1,
    end_line: int = 200,
) -> dict[str, object]:
    """Return bounded line-level Git provenance for a repository file."""
    return _invoke(lambda: git.blame(path=path, start_line=start_line, end_line=end_line))


@mcp.tool()
def docker_containers(include_stopped: bool = True) -> dict[str, object]:
    """List only containers in the configured Compose project."""
    return _invoke(lambda: docker.containers(include_stopped=include_stopped))


@mcp.tool()
def docker_inspect(container: str) -> dict[str, object]:
    """Return a secret-safe summary for one project container."""
    return _invoke(lambda: docker.inspect(container))


@mcp.tool()
def docker_logs(
    container: str,
    tail: int = 100,
    since_seconds: int = 300,
) -> dict[str, object]:
    """Return bounded, redacted logs for one project container."""
    return _invoke(
        lambda: docker.logs(
            container,
            tail=tail,
            since_seconds=since_seconds,
        )
    )


def _invoke(operation: Callable[[], ResultT]) -> ResultT:
    try:
        return operation()
    except (RuntimeError, ValueError) as error:
        raise ToolError(str(error)) from error


def main() -> None:
    mcp.run(
        transport="streamable-http",
        host=MCP_HOST,
        port=MCP_PORT,
        stateless_http=True,
        max_request_body_size=65_536,
        max_sessions=100,
    )


if __name__ == "__main__":
    main()
