import argparse
import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from aiops_core.remediation_models import RemediationCandidate
from aiops_core.validation_models import ValidationReport


def publish(
    *,
    candidate_path: Path,
    validation_path: Path,
    api_url: str,
) -> dict[str, object]:
    candidate = RemediationCandidate.model_validate_json(candidate_path.read_text())
    validation = ValidationReport.model_validate_json(validation_path.read_text())
    if validation.candidate_id != candidate.id:
        raise ValueError("validation report does not match candidate")
    candidate_response = _post(
        f"{api_url.rstrip('/')}/api/v1/candidates",
        candidate.model_dump(mode="json"),
    )
    validation_response = _post(
        f"{api_url.rstrip('/')}/api/v1/candidates/{candidate.id}/validation",
        validation.model_dump(mode="json"),
    )
    return {
        "candidate": candidate_response,
        "validation": validation_response,
        "dashboard": f"{api_url.rstrip('/')}/dashboard",
    }


def _post(url: str, payload: dict[str, object]) -> object:
    request = Request(
        url,
        data=json.dumps(payload, separators=(",", ":"), sort_keys=True).encode(),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urlopen(request, timeout=30) as response:
            return json.loads(response.read())
    except HTTPError as error:
        detail = error.read(16_384).decode(errors="replace")
        raise RuntimeError(f"API returned HTTP {error.code}: {detail}") from error


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Publish a candidate and validation report to the dashboard."
    )
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--validation", type=Path, required=True)
    parser.add_argument("--api-url", default="http://127.0.0.1:8081")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = publish(
        candidate_path=args.candidate.resolve(),
        validation_path=args.validation.resolve(),
        api_url=args.api_url,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
