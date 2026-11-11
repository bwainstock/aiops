import pytest
from aiops_loadgen.main import request_target, run_load


def test_request_targets_are_deterministic() -> None:
    assert [request_target(index) for index in range(12)] == [
        "/healthz",
        "/api/items?offset=1&limit=2",
        "/api/items?offset=2&limit=2",
        "/api/items?offset=3&limit=2",
        "/api/items?offset=4&limit=2",
        "/api/items?offset=0&limit=2",
        "/api/items?offset=1&limit=2",
        "/api/items?offset=2&limit=2",
        "/api/items?offset=3&limit=2",
        "/api/items?offset=4&limit=2",
        "/healthz",
        "/api/items?offset=1&limit=2",
    ]


@pytest.mark.parametrize(
    ("request_count", "timeout", "interval"),
    [(0, 1.0, 0.0), (1, 0.0, 0.0), (1, 1.0, -0.1)],
)
def test_load_rejects_invalid_limits(
    request_count: int,
    timeout: float,
    interval: float,
) -> None:
    with pytest.raises(ValueError):
        run_load(
            base_url="http://127.0.0.1:8000",
            request_count=request_count,
            timeout=timeout,
            interval=interval,
        )
