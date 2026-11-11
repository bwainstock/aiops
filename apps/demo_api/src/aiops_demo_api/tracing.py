import os

from fastapi import FastAPI
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor


def tracer_provider_from_environment(
    *,
    service_name: str,
    version: str,
    commit_sha: str,
) -> TracerProvider | None:
    if "OTEL_EXPORTER_OTLP_ENDPOINT" not in os.environ:
        return None

    provider = TracerProvider(
        resource=Resource.create(
            {
                "service.name": service_name,
                "service.version": version,
                "service.instance.id": commit_sha,
                "deployment.environment.name": "local",
            }
        )
    )
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    return provider


def instrument_app(app: FastAPI, tracer_provider: TracerProvider) -> None:
    FastAPIInstrumentor.instrument_app(
        app,
        tracer_provider=tracer_provider,
        excluded_urls="healthz,metrics",
    )
