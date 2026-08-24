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


def test_dingtalk_settings_are_masked_and_test_endpoint_is_protected(tmp_path, monkeypatch):
    monkeypatch.setenv("ADMIN_PASSWORD", "password")
    monkeypatch.setenv("APP_SECRET", "secret")
    app = create_app(tmp_path)
    app.state.alert_engine.send_test = AsyncMock()
    with TestClient(app) as client:
        assert client.post("/api/alerts/test").status_code == 401
        client.post("/api/login", json={"password": "password"})
        response = client.put("/api/settings", json={
            "scope_type": "organization",
            "scope": "acme",
            "api_url": "https://api.github.com",
            "token": "github_pat_example",
            "refresh_interval": 30,
            "alerts": {
                "enabled": False,
                "webhook": "https://oapi.dingtalk.com/robot/send?access_token=sensitive-token",
                "secret": "SEC-sensitive",
                "offline_after": 120,
                "recovery_enabled": True,
                "default_mentions": ["13800138000"],
                "routes": [{
                    "match_type": "label", "match_value": "gpu", "mentions": ["13900139000"]
                }],
            },
        })
        assert response.status_code == 200
        alerts = client.get("/api/settings").json()["alerts"]
        assert alerts["has_webhook"] is True
        assert alerts["has_secret"] is True
        assert "sensitive-token" not in str(alerts)
        assert "SEC-sensitive" not in str(alerts)
        assert alerts["routes"][0]["match_value"] == "gpu"

        assert client.post("/api/alerts/test").status_code == 200
        app.state.alert_engine.send_test.assert_awaited_once()


def test_dingtalk_webhook_rejects_non_official_hosts(tmp_path, monkeypatch):
    monkeypatch.setenv("ADMIN_PASSWORD", "password")
    monkeypatch.setenv("APP_SECRET", "secret")
    with TestClient(create_app(tmp_path)) as client:
        client.post("/api/login", json={"password": "password"})
        response = client.put("/api/settings", json={
            "scope_type": "organization", "scope": "acme",
            "api_url": "https://api.github.com", "token": "token", "refresh_interval": 30,
            "alerts": {
                "enabled": True,
                "webhook": "https://example.com/collect",
                "offline_after": 120,
                "recovery_enabled": True,
                "default_mentions": [],
                "routes": [],
            },
        })
        assert response.status_code == 422


def test_dingtalk_accepts_ten_second_offline_threshold(tmp_path, monkeypatch):
    monkeypatch.setenv("ADMIN_PASSWORD", "password")
    monkeypatch.setenv("APP_SECRET", "secret")
    with TestClient(create_app(tmp_path)) as client:
        client.post("/api/login", json={"password": "password"})
        response = client.put("/api/settings", json={
            "scope_type": "organization", "scope": "acme",
            "api_url": "https://api.github.com", "token": "token", "refresh_interval": 30,
            "alerts": {
                "enabled": False,
                "offline_after": 10,
                "recovery_enabled": True,
                "default_mentions": [],
                "routes": [],
            },
        })
        assert response.status_code == 200
        assert client.get("/api/settings").json()["alerts"]["offline_after"] == 10
