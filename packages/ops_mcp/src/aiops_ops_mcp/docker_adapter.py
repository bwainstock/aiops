import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import cast

from aiops_ops_mcp.http_client import BoundedHttpClient
from aiops_ops_mcp.security import redact_text


@dataclass(frozen=True)
class DockerAdapter:
    client: BoundedHttpClient
    compose_project: str = "aiops-lab"

    def containers(self, *, include_stopped: bool = True) -> dict[str, object]:
        filters = json.dumps(
            {
                "label": [
                    f"com.docker.compose.project={self.compose_project}",
                ]
            },
            separators=(",", ":"),
        )
        payload = self.client.get_json_value(
            "/containers/json",
            parameters={
                "all": 1 if include_stopped else 0,
                "filters": filters,
            },
        )
        if not isinstance(payload, list):
            raise RuntimeError("Docker response is not a container list")
        containers = cast(list[object], payload)
        return {
            "containers": [self._container_summary(item) for item in containers],
        }

    def inspect(self, container: str) -> dict[str, object]:
        container_id = self._resolve_container(container)
        payload = self.client.get_json(f"/containers/{container_id}/json")
        config_value = payload.get("Config")
        state_value = payload.get("State")
        network_value = payload.get("NetworkSettings")
        if not isinstance(config_value, dict) or not isinstance(state_value, dict):
            raise RuntimeError("Docker inspect response is incomplete")
        config = cast(dict[str, object], config_value)
        state = cast(dict[str, object], state_value)
        labels_value = config.get("Labels")
        safe_labels = self._safe_labels(
            cast(dict[object, object], labels_value) if isinstance(labels_value, dict) else {}
        )
        health_value = state.get("Health")
        health = cast(dict[str, object], health_value) if isinstance(health_value, dict) else None
        health_status = health.get("Status") if health is not None else None
        networks: list[str] = []
        if isinstance(network_value, dict):
            network = cast(dict[str, object], network_value)
            networks_value = network.get("Networks")
            if isinstance(networks_value, dict):
                network_map = cast(dict[object, object], networks_value)
                networks = sorted(str(name) for name in network_map)
        return {
            "id": payload.get("Id"),
            "name": payload.get("Name"),
            "image": config.get("Image"),
            "state": {
                "status": state.get("Status"),
                "running": state.get("Running"),
                "started_at": state.get("StartedAt"),
                "finished_at": state.get("FinishedAt"),
                "exit_code": state.get("ExitCode"),
                "health": health_status,
            },
            "labels": safe_labels,
            "networks": networks,
        }

    def logs(
        self,
        container: str,
        *,
        tail: int = 100,
        since_seconds: int = 300,
    ) -> dict[str, object]:
        if not 1 <= tail <= 500:
            raise ValueError("tail must be between 1 and 500")
        if not 0 <= since_seconds <= 3_600:
            raise ValueError("since_seconds must be between 0 and 3600")
        container_id = self._resolve_container(container)
        since = int((datetime.now(UTC) - timedelta(seconds=since_seconds)).timestamp())
        payload = self.client.get_bytes(
            f"/containers/{container_id}/logs",
            parameters={
                "stdout": 1,
                "stderr": 1,
                "timestamps": 1,
                "tail": tail,
                "since": since,
            },
        )
        text = redact_text(_decode_docker_logs(payload), max_length=65_536)
        return {
            "container_id": container_id,
            "logs": text,
            "tail": tail,
            "since_seconds": since_seconds,
        }

    def _resolve_container(self, value: str) -> str:
        if not value or value.startswith("-") or len(value) > 200:
            raise ValueError("invalid container identifier")
        response = self.containers(include_stopped=True)
        containers = response["containers"]
        if not isinstance(containers, list):
            raise RuntimeError("container inventory is invalid")
        matches: list[str] = []
        for container_value in cast(list[object], containers):
            if not isinstance(container_value, dict):
                continue
            container = cast(dict[str, object], container_value)
            container_id = str(container.get("id", ""))
            names_value = container.get("names")
            names = (
                [str(name) for name in cast(list[object], names_value)]
                if isinstance(names_value, list)
                else []
            )
            labels_value = container.get("labels")
            labels = cast(dict[str, object], labels_value) if isinstance(labels_value, dict) else {}
            service = labels.get("service")
            if container_id.startswith(value) or value in names or value == service:
                matches.append(container_id)
        if len(matches) != 1:
            raise ValueError("container must match exactly one allowed project container")
        return matches[0]

    def _container_summary(self, value: object) -> dict[str, object]:
        if not isinstance(value, dict):
            raise RuntimeError("Docker returned an invalid container")
        container = cast(dict[str, object], value)
        names_value = container.get("Names")
        names = cast(list[object], names_value) if isinstance(names_value, list) else []
        labels_value = container.get("Labels")
        labels = cast(dict[object, object], labels_value) if isinstance(labels_value, dict) else {}
        return {
            "id": container.get("Id"),
            "names": [str(name).removeprefix("/") for name in names if isinstance(name, str)],
            "image": container.get("Image"),
            "state": container.get("State"),
            "status": container.get("Status"),
            "labels": self._safe_labels(labels),
        }

    def _safe_labels(self, labels: dict[object, object]) -> dict[str, str]:
        allowed = {
            "com.docker.compose.project": "project",
            "com.docker.compose.service": "service",
            "com.docker.compose.container-number": "container_number",
        }
        return {
            target: str(labels[source]) for source, target in allowed.items() if source in labels
        }


def _decode_docker_logs(payload: bytes) -> str:
    if len(payload) < 8 or payload[0] not in {0, 1, 2}:
        return payload.decode(errors="replace")
    chunks: list[bytes] = []
    offset = 0
    while offset + 8 <= len(payload):
        size = int.from_bytes(payload[offset + 4 : offset + 8], byteorder="big")
        start = offset + 8
        end = start + size
        if end > len(payload):
            raise RuntimeError("Docker returned a truncated log frame")
        chunks.append(payload[start:end])
        offset = end
    return b"".join(chunks).decode(errors="replace")
