from __future__ import annotations

import base64
import hashlib
import json
import os
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path
from tempfile import NamedTemporaryFile

from cryptography.fernet import Fernet, InvalidToken


def default_alert_settings() -> dict[str, object]:
    return {
        "enabled": False,
        "webhook": "",
        "secret": "",
        "offline_after": 120,
        "recovery_enabled": True,
        "default_mentions": [],
        "routes": [],
    }


@dataclass(slots=True)
class AppConfig:
    scope_type: str = "organization"
    scope: str = ""
    api_url: str = "https://api.github.com"
    token: str = ""
    refresh_interval: int = 30
    alerts: dict[str, object] = field(default_factory=default_alert_settings)


class ConfigStore:
    """Persist configuration without ever exposing the token to the browser."""

    def __init__(self, data_dir: Path, secret: str) -> None:
        self.data_dir = data_dir
        self.path = data_dir / "config.json"
        self._lock = threading.RLock()
        digest = hashlib.sha256(secret.encode("utf-8")).digest()
        self._fernet = Fernet(base64.urlsafe_b64encode(digest))

    def load(self) -> AppConfig:
        with self._lock:
            if not self.path.exists():
                return AppConfig(token=os.getenv("GH_TOKEN", ""))
            try:
                payload = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return AppConfig()

            token = ""
            encrypted = payload.get("token", "")
            if encrypted:
                try:
                    token = self._fernet.decrypt(encrypted.encode("ascii")).decode("utf-8")
                except (InvalidToken, ValueError):
                    token = ""

            alerts = default_alert_settings()
            encrypted_alerts = payload.get("alerts", "")
            if encrypted_alerts:
                try:
                    decrypted = self._fernet.decrypt(encrypted_alerts.encode("ascii")).decode("utf-8")
                    stored_alerts = json.loads(decrypted)
                    if isinstance(stored_alerts, dict):
                        alerts.update(stored_alerts)
                except (InvalidToken, ValueError, json.JSONDecodeError):
                    pass

            return AppConfig(
                scope_type=payload.get("scope_type", "organization"),
                scope=payload.get("scope", ""),
                api_url=payload.get("api_url", "https://api.github.com"),
                token=token or os.getenv("GH_TOKEN", ""),
                refresh_interval=int(payload.get("refresh_interval", 30)),
                alerts=alerts,
            )

    def save(self, config: AppConfig) -> None:
        with self._lock:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            payload = asdict(config)
            payload["token"] = (
                self._fernet.encrypt(config.token.encode("utf-8")).decode("ascii")
                if config.token
                else ""
            )
            payload["alerts"] = self._fernet.encrypt(
                json.dumps(config.alerts, ensure_ascii=False).encode("utf-8")
            ).decode("ascii")
            with NamedTemporaryFile(
                "w", encoding="utf-8", dir=self.data_dir, delete=False, suffix=".tmp"
            ) as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                temp_path = Path(handle.name)
            temp_path.replace(self.path)
            try:
                self.path.chmod(0o600)
            except OSError:
                pass
