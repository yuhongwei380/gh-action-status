from pathlib import Path

from app.config import AppConfig, ConfigStore


def test_config_roundtrip_encrypts_token(tmp_path: Path):
    store = ConfigStore(tmp_path, "test-secret")
    store.save(AppConfig(
        scope="acme",
        token="github_pat_secret",
        alerts={
            "enabled": True,
            "webhook": "https://oapi.dingtalk.com/robot/send?access_token=sensitive",
            "secret": "SEC-sensitive",
            "offline_after": 120,
            "recovery_enabled": True,
            "default_mentions": ["13800138000"],
            "routes": [],
        },
    ))

    raw = (tmp_path / "config.json").read_text(encoding="utf-8")
    assert "github_pat_secret" not in raw
    assert "SEC-sensitive" not in raw
    assert "13800138000" not in raw
    loaded = store.load()
    assert loaded.scope == "acme"
    assert loaded.token == "github_pat_secret"
    assert loaded.alerts["secret"] == "SEC-sensitive"
    assert loaded.alerts["default_mentions"] == ["13800138000"]


def test_wrong_secret_cannot_decrypt_token(tmp_path: Path):
    ConfigStore(tmp_path, "first-secret").save(AppConfig(scope="acme", token="secret"))
    loaded = ConfigStore(tmp_path, "different-secret").load()
    assert loaded.token == ""
