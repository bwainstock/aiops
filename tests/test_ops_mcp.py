# pyright: reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownVariableType=false

import json
import subprocess
from collections.abc import Generator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.parse import urlparse

import pytest
from aiops_ops_mcp.docker_adapter import DockerAdapter
from aiops_ops_mcp.git_adapter import GitAdapter
from aiops_ops_mcp.http_client import BoundedHttpClient
from aiops_ops_mcp.loki import LokiAdapter
from aiops_ops_mcp.prometheus import PrometheusAdapter

RouteValue = tuple[int, str, bytes]


@contextmanager
def fixture_server(routes: dict[str, RouteValue]) -> Generator[str]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            path = urlparse(self.path).path
            status, content_type, payload = routes.get(
                path,
                (404, "application/json", b'{"error":"not found"}'),
            )
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args: object) -> None:
            del format
            del args

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def json_route(value: object) -> RouteValue:
    return 200, "application/json", json.dumps(value).encode()


def test_prometheus_adapter_bounds_queries_and_results() -> None:
    routes = {
        "/api/v1/query": json_route(
            {
                "status": "success",
                "data": {
                    "resultType": "vector",
                    "result": [{"metric": {"job": "demo-api"}, "value": [1, "1"]}],
                },
            }
        ),
        "/api/v1/query_range": json_route(
            {
                "status": "success",
                "data": {
                    "resultType": "matrix",
                    "result": [
                        {
                            "metric": {"route": "/api/items"},
                            "values": [[1, "1"], [2, "2"]],
                        }
                    ],
                },
            }
        ),
    }
    with fixture_server(routes) as base_url:
        adapter = PrometheusAdapter(BoundedHttpClient(base_url))
        instant = adapter.query('up{job="demo-api"}')
        ranged = adapter.query_range(
            "demo_http_requests_total",
            start="2026-11-11T22:00:00Z",
            end="2026-11-11T22:10:00Z",
            step_seconds=30,
        )

    assert instant["status"] == "success"
    assert ranged["status"] == "success"
    with pytest.raises(ValueError, match="time range"):
        adapter.query_range(
            "up",
            start="2026-11-11T20:00:00Z",
            end="2026-11-11T22:00:00Z",
            step_seconds=30,
        )
    with pytest.raises(ValueError, match="point limit"):
        adapter.query_range(
            "up",
            start="2026-11-11T22:00:00Z",
            end="2026-11-11T23:00:00Z",
            step_seconds=1,
        )


def test_loki_adapter_redacts_secrets_and_limits_ranges() -> None:
    routes = {
        "/loki/api/v1/query_range": json_route(
            {
                "status": "success",
                "data": {
                    "resultType": "streams",
                    "result": [
                        {
                            "stream": {"service": "demo-api"},
                            "values": [
                                [
                                    "1",
                                    "Authorization: Bearer abc.def token=supersecret",
                                ]
                            ],
                        }
                    ],
                },
            }
        )
    }
    with fixture_server(routes) as base_url:
        adapter = LokiAdapter(BoundedHttpClient(base_url))
        result = adapter.query_range(
            '{service="demo-api"}',
            start="2026-11-11T22:00:00Z",
            end="2026-11-11T22:05:00Z",
            limit=10,
        )

    encoded = json.dumps(result)
    assert "abc.def" not in encoded
    assert "supersecret" not in encoded
    assert "[REDACTED]" in encoded
    with pytest.raises(ValueError, match="limit"):
        adapter.query_range(
            "{}",
            start="2026-11-11T22:00:00Z",
            end="2026-11-11T22:05:00Z",
            limit=201,
        )


def test_git_adapter_is_read_only_bounded_and_path_restricted(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    _git(repository, "init", "-b", "main")
    _git(repository, "config", "user.email", "test@example.com")
    _git(repository, "config", "user.name", "Test User")
    tracked = repository / "app.txt"
    tracked.write_text("first\ntoken=supersecret\n")
    _git(repository, "add", "app.txt")
    _git(repository, "commit", "-m", "first")
    first_revision = _git(repository, "rev-parse", "HEAD").strip()
    tracked.write_text("first\nsecond\n")
    _git(repository, "add", "app.txt")
    _git(repository, "commit", "-m", "second")

    adapter = GitAdapter(repository)
    log = adapter.log(limit=2)
    shown = adapter.show(first_revision)
    diff = adapter.diff(base=first_revision)
    blamed = adapter.blame(path="app.txt", start_line=1, end_line=2)

    assert "second" in str(log["output"])
    assert "supersecret" not in str(shown["output"])
    assert "token=[REDACTED]" in str(shown["output"])
    assert "+second" in str(diff["output"])
    assert "filename app.txt" in str(blamed["output"])
    with pytest.raises(ValueError, match="escapes"):
        adapter.show(path="../outside.txt")
    with pytest.raises(ValueError, match="revision"):
        adapter.show("--help")


def test_docker_adapter_returns_safe_project_data_and_redacted_logs() -> None:
    container_id = "a" * 64
    log_payload = _docker_frame(b"token=supersecret\n")
    routes = {
        "/containers/json": json_route(
            [
                {
                    "Id": container_id,
                    "Names": ["/aiops-lab-demo-api-1"],
                    "Image": "aiops-lab-demo-api",
                    "State": "running",
                    "Status": "Up",
                    "Labels": {
                        "com.docker.compose.project": "aiops-lab",
                        "com.docker.compose.service": "demo-api",
                        "private": "do-not-return",
                    },
                }
            ]
        ),
        f"/containers/{container_id}/json": json_route(
            {
                "Id": container_id,
                "Name": "/aiops-lab-demo-api-1",
                "Config": {
                    "Image": "aiops-lab-demo-api",
                    "Env": ["TOKEN=supersecret"],
                    "Labels": {
                        "com.docker.compose.project": "aiops-lab",
                        "com.docker.compose.service": "demo-api",
                        "private": "do-not-return",
                    },
                },
                "State": {
                    "Status": "running",
                    "Running": True,
                    "StartedAt": "2026-11-11T22:00:00Z",
                    "FinishedAt": "",
                    "ExitCode": 0,
                    "Health": {"Status": "healthy"},
                },
                "NetworkSettings": {"Networks": {"aiops-lab_app": {}}},
            }
        ),
        f"/containers/{container_id}/logs": (200, "application/octet-stream", log_payload),
    }
    with fixture_server(routes) as base_url:
        adapter = DockerAdapter(BoundedHttpClient(base_url))
        containers = adapter.containers()
        inspected = adapter.inspect("demo-api")
        logs = adapter.logs("demo-api", tail=10, since_seconds=60)
        with pytest.raises(ValueError, match="exactly one"):
            adapter.inspect("missing")

    container_values = containers["containers"]
    assert isinstance(container_values, list)
    first_container = container_values[0]
    assert isinstance(first_container, dict)
    assert first_container["labels"] == {
        "project": "aiops-lab",
        "service": "demo-api",
    }
    assert "Env" not in inspected
    assert "private" not in json.dumps(inspected)
    assert "supersecret" not in str(logs["logs"])
    assert "token=[REDACTED]" in str(logs["logs"])
    assert not hasattr(adapter, "start")


def _git(repository: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=repository,
        capture_output=True,
        check=True,
        text=True,
    )
    return completed.stdout


def _docker_frame(payload: bytes) -> bytes:
    return b"\x01\x00\x00\x00" + len(payload).to_bytes(4, "big") + payload
