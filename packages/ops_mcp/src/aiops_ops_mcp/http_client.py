import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin
from urllib.request import Request, urlopen

from aiops_ops_mcp.security import redact_json


@dataclass(frozen=True)
class BoundedHttpClient:
    base_url: str
    timeout_seconds: float = 5
    max_response_bytes: int = 1_048_576

    def get_json(
        self,
        path: str,
        *,
        parameters: Mapping[str, str | int | float] | None = None,
    ) -> dict[str, object]:
        decoded = self.get_json_value(path, parameters=parameters)
        if not isinstance(decoded, dict):
            raise RuntimeError("upstream JSON response must be an object")
        return cast(dict[str, object], decoded)

    def get_json_value(
        self,
        path: str,
        *,
        parameters: Mapping[str, str | int | float] | None = None,
    ) -> object:
        payload = self.get_bytes(path, parameters=parameters)
        try:
            decoded = json.loads(payload)
        except json.JSONDecodeError as error:
            raise RuntimeError("upstream returned invalid JSON") from error
        return redact_json(decoded)

    def get_bytes(
        self,
        path: str,
        *,
        parameters: Mapping[str, str | int | float] | None = None,
    ) -> bytes:
        query = urlencode(parameters or {})
        url = urljoin(f"{self.base_url.rstrip('/')}/", path.lstrip("/"))
        if query:
            url = f"{url}?{query}"
        request = Request(url, headers={"Accept": "application/json"})
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                body = response.read(self.max_response_bytes + 1)
        except HTTPError as error:
            error.read(self.max_response_bytes)
            raise RuntimeError(f"upstream returned HTTP {error.code}") from error
        except URLError as error:
            raise RuntimeError(f"upstream request failed: {error.reason}") from error
        if len(body) > self.max_response_bytes:
            raise RuntimeError("upstream response exceeded the configured byte limit")
        return body
