from pathlib import Path

from app.config import AppConfig, ConfigStore


def test_config_roundtrip_encrypts_token(tmp_path: Path):
    store = ConfigStore(tmp_path, "test-secret")
    store.save(AppConfig(scope="acme", token="github_pat_secret"))

    raw = (tmp_path / "config.json").read_text(encoding="utf-8")
    assert "github_pat_secret" not in raw
    loaded = store.load()
    assert loaded.scope == "acme"
    assert loaded.token == "github_pat_secret"


def test_wrong_secret_cannot_decrypt_token(tmp_path: Path):
    ConfigStore(tmp_path, "first-secret").save(AppConfig(scope="acme", token="secret"))
    loaded = ConfigStore(tmp_path, "different-secret").load()
    assert loaded.token == ""

