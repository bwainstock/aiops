import argparse
import json
from collections import Counter
from dataclasses import asdict, dataclass
from time import perf_counter
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class LoadSummary:
    requests: int
    successes: int
    failures: int
    status_counts: dict[str, int]
    duration_seconds: float


def request_target(index: int) -> str:
    if index % 10 == 0:
        return "/healthz"
    offset = index % 5
    return f"/api/items?offset={offset}&limit=2"


def run_load(*, base_url: str, request_count: int, timeout: float) -> LoadSummary:
    if request_count < 1:
        raise ValueError("request_count must be at least 1")
    if timeout <= 0:
        raise ValueError("timeout must be positive")

    statuses: Counter[str] = Counter()
    successes = 0
    started_at = perf_counter()

    for index in range(request_count):
        target = request_target(index)
        request = Request(
            urljoin(f"{base_url.rstrip('/')}/", target.lstrip("/")),
            headers={"X-Request-ID": f"loadgen-{index:06d}"},
        )
        try:
            with urlopen(request, timeout=timeout) as response:
                status = response.status
                response.read()
        except HTTPError as error:
            status = error.code
            error.read()
        except URLError:
            statuses["network-error"] += 1
            continue

        statuses[str(status)] += 1
        if 200 <= status < 400:
            successes += 1

    return LoadSummary(
        requests=request_count,
        successes=successes,
        failures=request_count - successes,
        status_counts=dict(sorted(statuses.items())),
        duration_seconds=round(perf_counter() - started_at, 3),
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate deterministic demo API traffic.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--requests", type=int, default=100)
    parser.add_argument("--timeout", type=float, default=2.0)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    summary = run_load(
        base_url=args.base_url,
        request_count=args.requests,
        timeout=args.timeout,
    )
    print(json.dumps(asdict(summary), separators=(",", ":"), sort_keys=True))
    raise SystemExit(0 if summary.failures == 0 else 1)
