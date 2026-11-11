import os
from typing import Annotated, Literal

from fastapi import FastAPI, Query
from pydantic import BaseModel, ConfigDict, Field
from starlette.responses import Response

from aiops_demo_api.telemetry import HttpTelemetryMiddleware, Telemetry

SERVICE_NAME = "demo-api"


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BuildMetadata(ApiModel):
    version: str
    commit_sha: str


class HealthResponse(ApiModel):
    status: Literal["ok"]
    service: str
    build: BuildMetadata


class Item(ApiModel):
    id: int = Field(ge=1)
    name: str
    price_cents: int = Field(ge=0)


class ItemsQuery(ApiModel):
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=10, ge=1, le=100)


class ItemsResponse(ApiModel):
    items: list[Item]
    total: int = Field(ge=0)
    offset: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    build: BuildMetadata


ITEMS = (
    Item(id=1, name="anvil", price_cents=12500),
    Item(id=2, name="compass", price_cents=3200),
    Item(id=3, name="lantern", price_cents=4800),
    Item(id=4, name="rope", price_cents=1900),
    Item(id=5, name="telescope", price_cents=21500),
)


def build_metadata_from_environment() -> BuildMetadata:
    return BuildMetadata(
        version=os.environ.get("BUILD_VERSION", "dev"),
        commit_sha=os.environ.get("COMMIT_SHA", "unknown"),
    )


def create_app(build: BuildMetadata | None = None) -> FastAPI:
    metadata = build or build_metadata_from_environment()
    telemetry = Telemetry(version=metadata.version, commit_sha=metadata.commit_sha)
    app = FastAPI(title="AIOps Demo API", version=metadata.version)
    app.add_middleware(HttpTelemetryMiddleware, telemetry=telemetry)

    @app.get("/healthz", response_model=HealthResponse)
    def healthz() -> HealthResponse:
        return HealthResponse(status="ok", service=SERVICE_NAME, build=metadata)

    @app.get("/metrics", include_in_schema=False)
    def metrics() -> Response:
        return telemetry.response()

    @app.get("/api/items", response_model=ItemsResponse)
    def get_items(query: Annotated[ItemsQuery, Query()]) -> ItemsResponse:
        return ItemsResponse(
            items=list(ITEMS[query.offset : query.offset + query.limit]),
            total=len(ITEMS),
            offset=query.offset,
            limit=query.limit,
            build=metadata,
        )

    return app


app = create_app()
