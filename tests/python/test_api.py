"""Flask route tests via the test client. The fast tests cover the request
guards (no solving); the happy-path /api/generate is marked slow.
"""
import pytest

from app import app as flask_app
from helpers import make_division, make_config


@pytest.fixture
def client():
    flask_app.config["TESTING"] = True
    return flask_app.test_client()


def test_health(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "ok"
    assert body["solver"] == "cp-sat"


def test_generate_rejects_empty_config(client):
    resp = client.post("/api/generate", json={})
    assert resp.status_code == 400
    assert "error" in resp.get_json()


def test_validate_requires_config_and_games(client):
    resp = client.post("/api/validate", json={"config": {}})  # missing 'games'
    assert resp.status_code == 400
    assert "error" in resp.get_json()


def test_health_report_requires_config_and_games(client):
    resp = client.post("/api/health-report", json={"games": []})  # missing 'config'
    assert resp.status_code == 400


def test_progress_endpoint_returns_json(client):
    resp = client.get("/api/progress")
    assert resp.status_code == 200
    assert isinstance(resp.get_json(), dict)


@pytest.mark.slow
def test_generate_happy_path(client):
    cfg = make_config(
        [make_division("U16 BOYS", [4])],
        sites=[{"name": "Blanes", "numCourts": 4}],
        setup={"nDays": 3},
    )
    cfg["solverTimeLimit"] = 15
    resp = client.post("/api/generate", json=cfg)
    assert resp.status_code == 200
    body = resp.get_json()
    assert "sched" in body
    assert body["sched"].get("gameDays")
