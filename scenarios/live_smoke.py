import argparse
import json
import subprocess
import time
from urllib.request import urlopen


def get_json(url: str) -> object:
    with urlopen(url, timeout=10) as response:
        return json.loads(response.read())


def wait_for_incident(alertname: str, *, attempts: int = 60) -> str:
    for _ in range(attempts):
        incidents = get_json("http://127.0.0.1:8081/api/v1/incidents")
        matches = [
            item
            for item in incidents
            if item["alertname"] == alertname and item["status"] == "open"
        ]
        if matches:
            matches.sort(key=lambda item: item["updated_at"], reverse=True)
            return matches[0]["id"]
        time.sleep(1)
    raise RuntimeError(f"{alertname} incident did not open")


def wait_for_resolution(incident_id: str, *, attempts: int = 90) -> None:
    for _ in range(attempts):
        detail = get_json(f"http://127.0.0.1:8081/api/v1/incidents/{incident_id}")
        if detail["incident"]["status"] == "resolved":
            return
        time.sleep(1)
    raise RuntimeError("incident did not resolve")


def run_error() -> dict[str, object]:
    load = subprocess.Popen(
        ["make", "trigger-error"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        incident_id = wait_for_incident("HighErrorRate")
        completed = subprocess.run(
            ["uv", "run", "aiops-triage", "--incident-id", incident_id],
            capture_output=True,
            check=True,
            text=True,
            timeout=600,
        )
        triage = json.loads(completed.stdout)
    finally:
        load.wait(timeout=120)
    wait_for_resolution(incident_id)
    return {
        "scenario": "error",
        "incident_id": incident_id,
        "triage_status": triage["status"],
        "validation_errors": triage["validation_errors"],
        "fail_closed": triage["status"] != "triaged",
    }


def run_latency() -> dict[str, object]:
    load = subprocess.Popen(
        ["make", "trigger-latency"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        incident_id = wait_for_incident("HighLatencyP95")
    finally:
        load.wait(timeout=120)
    wait_for_resolution(incident_id)
    return {
        "scenario": "latency",
        "incident_id": incident_id,
        "status": "resolved",
        "note": "Adaptive latency triage remains deferred by ADR-0001.",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run an opt-in live E2E smoke scenario.")
    parser.add_argument("scenario", choices=("error", "latency"))
    args = parser.parse_args()
    result = run_error() if args.scenario == "error" else run_latency()
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
