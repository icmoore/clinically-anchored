from fastapi.testclient import TestClient

from clinically_anchored_api.core.config import get_settings
from clinically_anchored_api.main import app

client = TestClient(app)  # not used as a context manager: startup checks don't run here


def test_health(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "app_env", "")
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "environment": "unset"}


def test_health_reports_the_environment(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "app_env", "staging")
    assert client.get("/health").json() == {"status": "ok", "environment": "staging"}
