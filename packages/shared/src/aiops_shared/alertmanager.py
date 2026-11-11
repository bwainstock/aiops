from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class AlertmanagerModel(BaseModel):
    model_config = ConfigDict(extra="allow")


class Alert(AlertmanagerModel):
    status: Literal["firing", "resolved"]
    labels: dict[str, str]
    annotations: dict[str, str]
    startsAt: datetime
    endsAt: datetime
    generatorURL: str
    fingerprint: str


class AlertmanagerWebhook(AlertmanagerModel):
    version: str
    groupKey: str
    truncatedAlerts: int = Field(ge=0)
    status: Literal["firing", "resolved"]
    receiver: str
    groupLabels: dict[str, str]
    commonLabels: dict[str, str]
    commonAnnotations: dict[str, str]
    externalURL: str
    alerts: list[Alert]
