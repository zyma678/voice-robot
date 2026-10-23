from fastapi.testclient import TestClient

from voice_robot.server import app


def test_versioned_console_assets_are_immutable() -> None:
    response = TestClient(app).get("/console/assets/app.js?v=20260712-perf")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "public, max-age=31536000, immutable"


def test_unversioned_console_assets_revalidate() -> None:
    response = TestClient(app).get("/console/assets/app.js")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-cache"
