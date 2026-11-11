from collections import deque
from datetime import UTC, datetime
from threading import Lock
from typing import Literal

from aiops_shared.alertmanager import AlertmanagerWebhook
from fastapi import FastAPI, Query, status
from pydantic import BaseModel, Field


class CapturedEvent(BaseModel):
    id: int = Field(ge=1)
    received_at: datetime
    payload: AlertmanagerWebhook


class CaptureResponse(BaseModel):
    id: int = Field(ge=1)


class DeleteResponse(BaseModel):
    deleted: int = Field(ge=0)


class HealthResponse(BaseModel):
    status: Literal["ok"]


class EventStore:
    def __init__(self, *, capacity: int = 500) -> None:
        self._events: deque[CapturedEvent] = deque(maxlen=capacity)
        self._next_id = 1
        self._lock = Lock()

    def append(self, payload: AlertmanagerWebhook) -> CapturedEvent:
        with self._lock:
            event = CapturedEvent(
                id=self._next_id,
                received_at=datetime.now(UTC),
                payload=payload,
            )
            self._events.append(event)
            self._next_id += 1
            return event

    def list(
        self,
        *,
        alertname: str | None,
        event_status: Literal["firing", "resolved"] | None,
    ) -> list[CapturedEvent]:
        with self._lock:
            events = list(self._events)

        if alertname is not None:
            events = [
                event
                for event in events
                if event.payload.commonLabels.get("alertname") == alertname
            ]
        if event_status is not None:
            events = [event for event in events if event.payload.status == event_status]
        return events

    def clear(self) -> int:
        with self._lock:
            deleted = len(self._events)
            self._events.clear()
            return deleted


def create_app(store: EventStore | None = None) -> FastAPI:
    event_store = store or EventStore()
    app = FastAPI(title="Alert Capture Sink", version="0.1.0")

    @app.get("/healthz", response_model=HealthResponse)
    def healthz() -> HealthResponse:
        return HealthResponse(status="ok")

    @app.post(
        "/api/v1/alerts",
        response_model=CaptureResponse,
        status_code=status.HTTP_202_ACCEPTED,
    )
    def capture(payload: AlertmanagerWebhook) -> CaptureResponse:
        event = event_store.append(payload)
        return CaptureResponse(id=event.id)

    @app.get("/api/v1/events", response_model=list[CapturedEvent])
    def list_events(
        alertname: str | None = None,
        event_status: Literal["firing", "resolved"] | None = Query(
            default=None,
            alias="status",
        ),
    ) -> list[CapturedEvent]:
        return event_store.list(alertname=alertname, event_status=event_status)

    @app.delete(
        "/api/v1/events",
        response_model=DeleteResponse,
        status_code=status.HTTP_200_OK,
    )
    def clear_events() -> DeleteResponse:
        return DeleteResponse(deleted=event_store.clear())

    return app


app = create_app()
