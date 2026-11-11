import json
import logging
import re
import sys
from datetime import UTC, datetime
from time import perf_counter
from typing import Final, cast
from uuid import uuid4

from opentelemetry import trace
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Counter, Gauge, Histogram
from prometheus_client.exposition import generate_latest
from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

SERVICE_NAME: Final = "demo-api"
REQUEST_ID_HEADER: Final = "X-Request-ID"
_REQUEST_ID_PATTERN: Final = re.compile(r"[A-Za-z0-9._-]{1,128}")
_HTTP_LOGGER: Final = logging.getLogger("aiops_demo_api.http")


def _configure_http_logger() -> None:
    if _HTTP_LOGGER.handlers:
        return

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(message)s"))
    _HTTP_LOGGER.addHandler(handler)
    _HTTP_LOGGER.setLevel(logging.INFO)
    _HTTP_LOGGER.propagate = False


_configure_http_logger()


class Telemetry:
    def __init__(self, *, version: str, commit_sha: str) -> None:
        self.registry = CollectorRegistry()
        self.requests = Counter(
            "demo_http_requests",
            "Completed HTTP requests.",
            ("method", "route", "status_code"),
            registry=self.registry,
        )
        self.duration = Histogram(
            "demo_http_request_duration_seconds",
            "HTTP request duration in seconds.",
            ("method", "route"),
            buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0),
            registry=self.registry,
        )
        self.in_progress = Gauge(
            "demo_http_requests_in_progress",
            "HTTP requests currently being processed.",
            ("method",),
            registry=self.registry,
        )
        self.build = Gauge(
            "demo_build_info",
            "Build identity for the running demo service.",
            ("version", "commit_sha"),
            registry=self.registry,
        )
        self.build.labels(version=version, commit_sha=commit_sha).set(1)

    def record_request(
        self,
        *,
        method: str,
        route: str,
        status_code: int,
        duration_seconds: float,
    ) -> None:
        self.requests.labels(
            method=method,
            route=route,
            status_code=str(status_code),
        ).inc()
        self.duration.labels(method=method, route=route).observe(duration_seconds)

    def response(self) -> Response:
        return Response(
            content=generate_latest(self.registry),
            headers={"Content-Type": CONTENT_TYPE_LATEST},
        )


class HttpTelemetryMiddleware:
    def __init__(self, app: ASGIApp, *, telemetry: Telemetry) -> None:
        self.app = app
        self.telemetry = telemetry

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = cast(str, scope.get("method", "UNKNOWN"))
        request_id = _request_id(Headers(scope=scope).get(REQUEST_ID_HEADER))
        state = scope.setdefault("state", {})
        state["request_id"] = request_id
        status_code = 500
        error_type: str | None = None
        started_at = perf_counter()
        self.telemetry.in_progress.labels(method=method).inc()

        async def send_with_request_id(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        except Exception as error:
            error_type = type(error).__name__
            raise
        finally:
            duration_seconds = perf_counter() - started_at
            route = _route_template(scope)
            self.telemetry.in_progress.labels(method=method).dec()
            self.telemetry.record_request(
                method=method,
                route=route,
                status_code=status_code,
                duration_seconds=duration_seconds,
            )
            _log_request(
                request_id=request_id,
                method=method,
                route=route,
                status_code=status_code,
                duration_seconds=duration_seconds,
                error_type=error_type,
            )


def _request_id(candidate: str | None) -> str:
    if candidate is not None and _REQUEST_ID_PATTERN.fullmatch(candidate):
        return candidate
    return uuid4().hex


def _route_template(scope: Scope) -> str:
    route = scope.get("route")
    path = getattr(route, "path", None)
    return path if isinstance(path, str) else "unmatched"


def _log_request(
    *,
    request_id: str,
    method: str,
    route: str,
    status_code: int,
    duration_seconds: float,
    error_type: str | None,
) -> None:
    payload: dict[str, object] = {
        "timestamp": datetime.now(UTC).isoformat(),
        "level": "error" if error_type is not None or status_code >= 500 else "info",
        "event": "http_request",
        "service": SERVICE_NAME,
        "request_id": request_id,
        "method": method,
        "route": route,
        "status_code": status_code,
        "duration_ms": round(duration_seconds * 1000, 3),
    }
    if error_type is not None:
        payload["error_type"] = error_type

    span_context = trace.get_current_span().get_span_context()
    if span_context.is_valid:
        payload["trace_id"] = f"{span_context.trace_id:032x}"
        payload["span_id"] = f"{span_context.span_id:016x}"

    _HTTP_LOGGER.info(json.dumps(payload, separators=(",", ":"), sort_keys=True))
