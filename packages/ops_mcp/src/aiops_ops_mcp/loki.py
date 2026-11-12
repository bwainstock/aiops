from dataclasses import dataclass
from typing import Literal, cast

from aiops_ops_mcp.http_client import BoundedHttpClient
from aiops_ops_mcp.time_range import validate_range


@dataclass(frozen=True)
class LokiAdapter:
    client: BoundedHttpClient
    max_query_length: int = 1_000
    max_range_seconds: int = 3_600
    max_lines: int = 200
    max_streams: int = 100

    def query_range(
        self,
        query: str,
        *,
        start: str,
        end: str,
        limit: int = 100,
        direction: Literal["forward", "backward"] = "backward",
    ) -> dict[str, object]:
        if not query.strip():
            raise ValueError("query must be non-empty")
        if len(query) > self.max_query_length:
            raise ValueError("query exceeds the configured length limit")
        if not 1 <= limit <= self.max_lines:
            raise ValueError(f"limit must be between 1 and {self.max_lines}")
        start_time, end_time = validate_range(
            start=start,
            end=end,
            max_seconds=self.max_range_seconds,
        )
        payload = self.client.get_json(
            "/loki/api/v1/query_range",
            parameters={
                "query": query,
                "start": int(start_time.timestamp() * 1_000_000_000),
                "end": int(end_time.timestamp() * 1_000_000_000),
                "limit": limit,
                "direction": direction,
            },
        )
        self._validate_response(payload, limit=limit)
        return payload

    def _validate_response(
        self,
        payload: dict[str, object],
        *,
        limit: int,
    ) -> None:
        if payload.get("status") != "success":
            raise RuntimeError("Loki query did not succeed")
        data_value = payload.get("data")
        if not isinstance(data_value, dict):
            raise RuntimeError("Loki response is missing data")
        data = cast(dict[str, object], data_value)
        result = data.get("result")
        if not isinstance(result, list):
            raise RuntimeError("Loki response is missing a result list")
        streams = cast(list[object], result)
        if len(streams) > self.max_streams:
            raise RuntimeError("Loki result exceeded the stream limit")
        lines = 0
        for stream_value in streams:
            if not isinstance(stream_value, dict):
                raise RuntimeError("Loki result contains an invalid stream")
            stream = cast(dict[str, object], stream_value)
            values = stream.get("values")
            if isinstance(values, list):
                lines += len(cast(list[object], values))
        if lines > limit:
            raise RuntimeError("Loki result exceeded the requested line limit")
