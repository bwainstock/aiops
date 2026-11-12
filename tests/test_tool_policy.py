import json
from pathlib import Path

EXPECTED_OPS_TOOLS = {
    "prometheus_query",
    "prometheus_query_range",
    "loki_query_range",
    "git_status",
    "git_log",
    "git_show",
    "git_diff",
    "git_blame",
    "docker_containers",
    "docker_inspect",
    "docker_logs",
}


def test_triage_tool_profile_is_explicit_and_read_only() -> None:
    policy_path = Path(__file__).parents[1] / "config" / "tool-profiles.json"
    policy = json.loads(policy_path.read_text())
    triage = policy["triage"]

    assert set(triage["ops_mcp"]["tools"]) == EXPECTED_OPS_TOOLS
    assert "*" not in triage["ops_mcp"]["tools"]
    assert triage["github_mcp"] == {
        "mode": "builtin-readonly",
        "allow_write": False,
    }
