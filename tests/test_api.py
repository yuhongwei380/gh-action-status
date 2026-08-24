from fastapi.testclient import TestClient
from unittest.mock import AsyncMock

from app.main import create_app


def test_auth_and_settings_flow(tmp_path, monkeypatch):
    monkeypatch.setenv("ADMIN_PASSWORD", "correct-horse")
    monkeypatch.setenv("APP_SECRET", "test-app-secret")
    with TestClient(create_app(tmp_path)) as client:
        assert client.get("/healthz").status_code == 200
        assert client.get("/api/settings").status_code == 401
        assert client.post("/api/login", json={"password": "wrong"}).status_code == 401
        assert client.post("/api/login", json={"password": "correct-horse"}).status_code == 200

        response = client.put("/api/settings", json={
            "scope_type": "repository",
            "scope": "acme/widgets",
            "api_url": "https://api.github.com",
            "token": "github_pat_example",
            "refresh_interval": 30,
        })
        assert response.status_code == 200
        settings = client.get("/api/settings").json()
        assert settings["scope"] == "acme/widgets"
        assert settings["token_hint"] == "••••mple"
        assert "token" not in settings


def test_repository_scope_is_validated(tmp_path, monkeypatch):
    monkeypatch.setenv("ADMIN_PASSWORD", "password")
    monkeypatch.setenv("APP_SECRET", "secret")
    with TestClient(create_app(tmp_path)) as client:
        client.post("/api/login", json={"password": "password"})
        response = client.put("/api/settings", json={
            "scope_type": "repository", "scope": "missing-slash",
            "api_url": "https://api.github.com", "token": "token", "refresh_interval": 30,
        })
        assert response.status_code == 422


def test_runner_status_is_public_but_force_refresh_requires_admin(tmp_path, monkeypatch):
    monkeypatch.setenv("ADMIN_PASSWORD", "password")
    monkeypatch.setenv("APP_SECRET", "secret")
    app = create_app(tmp_path)
    app.state.runners.snapshot = AsyncMock(return_value={"runners": [], "labels": [], "summary": {}})
    with TestClient(app) as client:
        response = client.get("/api/runners?force=true")
        assert response.status_code == 200
        app.state.runners.snapshot.assert_awaited_once_with(force=False)

        client.post("/api/login", json={"password": "password"})
        client.get("/api/runners?force=true")
        assert app.state.runners.snapshot.await_args_list[-1].kwargs == {"force": True}
