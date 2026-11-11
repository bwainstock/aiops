from importlib import import_module

import pytest

PACKAGES = (
    "aiops_alert_sink",
    "aiops_core",
    "aiops_demo_api",
    "aiops_loadgen",
    "aiops_ops_mcp",
    "aiops_shared",
)


@pytest.mark.parametrize("package", PACKAGES)
def test_workspace_package_is_importable(package: str) -> None:
    assert import_module(package).__name__ == package
