from fastapi.testclient import TestClient

from aiops_demo_api.main import BuildMetadata, create_app

BUILD = BuildMetadata(version="1.2.3", commit_sha="abc123def")
CLIENT = TestClient(create_app(BUILD))


def test_healthz_contract() -> None:
    response = CLIENT.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "demo-api",
        "build": {"version": "1.2.3", "commit_sha": "abc123def"},
    }


def test_items_contract_is_deterministic() -> None:
    response = CLIENT.get("/api/items", params={"offset": 1, "limit": 2})

    assert response.status_code == 200
    assert response.json() == {
        "items": [
            {"id": 2, "name": "compass", "price_cents": 3200},
            {"id": 3, "name": "lantern", "price_cents": 4800},
        ],
        "total": 5,
        "offset": 1,
        "limit": 2,
        "build": {"version": "1.2.3", "commit_sha": "abc123def"},
    }


def test_items_rejects_invalid_query() -> None:
    response = CLIENT.get("/api/items", params={"offset": -1, "limit": 101})

    assert response.status_code == 422


def test_build_metadata_comes_from_environment(monkeypatch: object) -> None:
    monkeypatch.setenv("BUILD_VERSION", "9.8.7")
    monkeypatch.setenv("COMMIT_SHA", "fedcba987")

    with TestClient(create_app()) as client:
        response = client.get("/healthz")

    assert response.json()["build"] == {
        "version": "9.8.7",
        "commit_sha": "fedcba987",
    }
