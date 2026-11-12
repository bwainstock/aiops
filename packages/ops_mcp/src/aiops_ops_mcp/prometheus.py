from dataclasses import dataclass
from typing import cast

from aiops_ops_mcp.http_client import BoundedHttpClient
from aiops_ops_mcp.time_range import validate_range


@dataclass(frozen=True)
class PrometheusAdapter:
    client: BoundedHttpClient
    max_query_length: int = 1_000
    max_range_seconds: int = 3_600
    max_series: int = 100
    max_points: int = 2_000

    def query(self, query: str, *, time: str | None = None) -> dict[str, object]:
        self._validate_query(query)
        parameters: dict[str, str] = {"query": query}
        if time is not None:
            parameters["time"] = time
        payload = self.client.get_json("/api/v1/query", parameters=parameters)
        self._validate_response(payload)
        return payload

    def query_range(
        self,
        query: str,
        *,
        start: str,
        end: str,
        step_seconds: float,
    ) -> dict[str, object]:
        self._validate_query(query)
        start_time, end_time = validate_range(
            start=start,
            end=end,
            max_seconds=self.max_range_seconds,
        )
        if step_seconds < 1:
            raise ValueError("step_seconds must be at least 1")
        expected_points = int((end_time - start_time).total_seconds() / step_seconds) + 1
        if expected_points > self.max_points:
            raise ValueError("query would exceed the point limit")
        payload = self.client.get_json(
            "/api/v1/query_range",
            parameters={
                "query": query,
                "start": start_time.timestamp(),
                "end": end_time.timestamp(),
                "step": step_seconds,
            },
        )
        self._validate_response(payload)
        return payload

    def _validate_query(self, query: str) -> None:
        if not query.strip():
            raise ValueError("query must be non-empty")
        if len(query) > self.max_query_length:
            raise ValueError("query exceeds the configured length limit")

    def _validate_response(self, payload: dict[str, object]) -> None:
        if payload.get("status") != "success":
            raise RuntimeError("Prometheus query did not succeed")
        data_value = payload.get("data")
        if not isinstance(data_value, dict):
            raise RuntimeError("Prometheus response is missing data")
        data = cast(dict[str, object], data_value)
        result = data.get("result")
        if not isinstance(result, list):
            raise RuntimeError("Prometheus response is missing a result list")
        series_list = cast(list[object], result)
        if len(series_list) > self.max_series:
            raise RuntimeError("Prometheus result exceeded the series limit")
        points = 0
        for series_value in series_list:
            if not isinstance(series_value, dict):
                raise RuntimeError("Prometheus result contains an invalid series")
            series = cast(dict[str, object], series_value)
            values = series.get("values")
            if isinstance(values, list):
                points += len(cast(list[object], values))
            elif "value" in series:
                points += 1
        if points > self.max_points:
            raise RuntimeError("Prometheus result exceeded the point limit")
