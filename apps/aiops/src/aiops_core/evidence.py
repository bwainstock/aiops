from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import uuid4

from mcp import Client

from aiops_core.models import DeploymentRecord, IncidentDetail
from aiops_core.triage_models import EvidenceBundle, EvidenceItem, EvidenceSource
from aiops_core.triage_validation import evidence_bundle_digest, evidence_item_digest


class EvidenceCollector:
    def __init__(self, mcp_url: str) -> None:
        self.mcp_url = mcp_url

    async def collect(
        self,
        incident: IncidentDetail,
        deployments: list[DeploymentRecord],
    ) -> EvidenceBundle:
        alert_event = next(
            event for event in reversed(incident.events) if event.kind == "alert.firing"
        )
        alert_payload = alert_event.payload.get("alert")
        if not isinstance(alert_payload, dict):
            raise ValueError("firing alert payload is missing")
        alert_data = cast(dict[str, object], alert_payload)
        starts_at_value = alert_data.get("startsAt")
        if not isinstance(starts_at_value, str):
            raise ValueError("firing alert start time is missing")
        alert_start = datetime.fromisoformat(starts_at_value.replace("Z", "+00:00"))
        window_start = alert_start - timedelta(minutes=2)
        window_end = max(datetime.now(UTC), alert_start + timedelta(minutes=2))
        if window_end - window_start > timedelta(hours=1):
            window_start = window_end - timedelta(hours=1)

        items = [
            self._item(
                source="alertmanager",
                kind="alert.firing",
                observed_at=alert_start,
                query=None,
                content=alert_event.payload,
            )
        ]
        if deployments:
            deployment = deployments[0]
            items.append(
                self._item(
                    source="deployment",
                    kind="deployment.latest",
                    observed_at=deployment.deployed_at,
                    query=None,
                    content=deployment.model_dump(mode="json"),
                )
            )

        async with Client(self.mcp_url) as client:
            ratio_result = await self._call(
                client,
                "prometheus_query",
                {
                    "query": (
                        'sum(rate(demo_http_requests_total{route="/api/items",'
                        'status_code=~"5.."}[30s])) / '
                        "clamp_min(sum(rate(demo_http_requests_total"
                        '{route="/api/items"}[30s])), 0.001)'
                    )
                },
            )
            items.append(
                self._item(
                    source="prometheus",
                    kind="error_ratio",
                    observed_at=datetime.now(UTC),
                    query="current 30s /api/items 5xx ratio",
                    content={
                        "value": _prometheus_scalar(ratio_result),
                        "result": ratio_result,
                    },
                )
            )
            logs = await self._call(
                client,
                "loki_query_range",
                {
                    "query": (
                        '{service="demo-api"} | json | route="/api/items" | status_code >= 500'
                    ),
                    "start": window_start.isoformat(),
                    "end": window_end.isoformat(),
                    "limit": 100,
                },
            )
            items.append(
                self._item(
                    source="loki",
                    kind="error_logs",
                    observed_at=datetime.now(UTC),
                    query="demo-api /api/items status >= 500",
                    content=logs,
                )
            )
            git_log = await self._call(
                client,
                "git_log",
                {"limit": 10},
            )
            items.append(
                self._item(
                    source="git",
                    kind="recent_commits",
                    observed_at=datetime.now(UTC),
                    query="git log -10",
                    content=git_log,
                )
            )
            docker_state = await self._call(
                client,
                "docker_inspect",
                {"container": "demo-api"},
            )
            items.append(
                self._item(
                    source="docker",
                    kind="container_state",
                    observed_at=datetime.now(UTC),
                    query="inspect demo-api",
                    content=docker_state,
                )
            )

        collected_at = datetime.now(UTC)
        bundle = EvidenceBundle(
            id=str(uuid4()),
            incident_id=incident.incident.id,
            collected_at=collected_at,
            window_start=window_start,
            window_end=window_end,
            items=items,
            digest="pending",
        )
        return bundle.model_copy(update={"digest": evidence_bundle_digest(bundle)})

    async def _call(
        self,
        client: Client,
        name: str,
        arguments: dict[str, object],
    ) -> dict[str, object]:
        result = await client.call_tool(name, arguments)
        if result.is_error:
            raise RuntimeError(f"evidence tool {name} failed: {result.content}")
        if result.structured_content is None:
            raise RuntimeError(f"evidence tool {name} returned no structured content")
        return cast(dict[str, object], result.structured_content)

    def _item(
        self,
        *,
        source: EvidenceSource,
        kind: str,
        observed_at: datetime,
        query: str | None,
        content: dict[str, object],
    ) -> EvidenceItem:
        item = EvidenceItem(
            id=f"ev-{uuid4()}",
            source=source,
            kind=kind,
            observed_at=observed_at,
            query=query,
            content=content,
            digest="pending",
        )
        return item.model_copy(update={"digest": evidence_item_digest(item)})


def _prometheus_scalar(payload: dict[str, object]) -> float:
    data_value = payload.get("data")
    if not isinstance(data_value, dict):
        return 0
    data = cast(dict[str, object], data_value)
    result = data.get("result")
    if not isinstance(result, list) or not result:
        return 0
    result_values = cast(list[object], result)
    first_value = result_values[0]
    if not isinstance(first_value, dict):
        return 0
    first = cast(dict[str, object], first_value)
    value = first.get("value")
    if not isinstance(value, list):
        return 0
    values = cast(list[object], value)
    if len(values) != 2:
        return 0
    try:
        return float(cast(str | int | float, values[1]))
    except (TypeError, ValueError):
        return 0
