from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from app.alerts import AlertEngine, AlertStateStore, DingTalkNotifier, RunnerState
from app.config import AppConfig


class RecordingNotifier:
    def __init__(self) -> None:
        self.messages: list[dict[str, object]] = []

    async def send(self, settings, title, text, mentions) -> None:
        self.messages.append({"title": title, "text": text, "mentions": mentions})


def snapshot(status: str) -> dict[str, object]:
    return {
        "runners": [{"id": 7, "name": "linux-build-01", "status": status, "labels": ["linux", "gpu"]}]
    }


def config() -> AppConfig:
    return AppConfig(
        scope="acme",
        alerts={
            "enabled": True,
            "webhook": "https://oapi.dingtalk.com/robot/send?access_token=test",
            "secret": "SEC-test",
            "offline_after": 120,
            "recovery_enabled": True,
            "default_mentions": ["13000000000"],
            "routes": [
                {"match_type": "label", "match_value": "gpu", "mentions": ["13100000000"]},
                {"match_type": "runner", "match_value": "linux-build-01", "mentions": ["13200000000"]},
            ],
        },
    )


def test_runner_route_has_priority_over_label_and_default(tmp_path: Path):
    engine = AlertEngine(AlertStateStore(tmp_path / "alerts.db"), RecordingNotifier())
    settings = config().alerts
    assert engine.resolve_mentions("linux-build-01", ["gpu"], settings) == ["13200000000"]
    assert engine.resolve_mentions("another", ["gpu"], settings) == ["13100000000"]
    assert engine.resolve_mentions("another", ["linux"], settings) == ["13000000000"]


def test_offline_transition_alerts_once_and_then_recovers(tmp_path: Path):
    notifier = RecordingNotifier()
    engine = AlertEngine(AlertStateStore(tmp_path / "alerts.db"), notifier)
    settings = config()

    asyncio.run(engine.process_snapshot(snapshot("online"), settings, now=0))
    asyncio.run(engine.process_snapshot(snapshot("offline"), settings, now=10))
    asyncio.run(engine.process_snapshot(snapshot("offline"), settings, now=129))
    assert notifier.messages == []

    asyncio.run(engine.process_snapshot(snapshot("offline"), settings, now=130))
    asyncio.run(engine.process_snapshot(snapshot("offline"), settings, now=500))
    assert len(notifier.messages) == 1
    assert "已离线" in str(notifier.messages[0]["title"])
    assert notifier.messages[0]["mentions"] == ["13200000000"]

    asyncio.run(engine.process_snapshot(snapshot("online"), settings, now=501))
    assert len(notifier.messages) == 2
    assert "已恢复在线" in str(notifier.messages[1]["title"])


def test_runner_first_observed_offline_alerts_and_then_recovers(tmp_path: Path):
    notifier = RecordingNotifier()
    engine = AlertEngine(AlertStateStore(tmp_path / "alerts.db"), notifier)
    settings = config()
    asyncio.run(engine.process_snapshot(snapshot("offline"), settings, now=100))
    asyncio.run(engine.process_snapshot(snapshot("offline"), settings, now=219))
    assert notifier.messages == []
    asyncio.run(engine.process_snapshot(snapshot("offline"), settings, now=220))
    assert len(notifier.messages) == 1
    assert "已离线" in str(notifier.messages[0]["title"])
    asyncio.run(engine.process_snapshot(snapshot("online"), settings, now=221))
    assert len(notifier.messages) == 2
    assert "已恢复在线" in str(notifier.messages[1]["title"])


def test_legacy_suppressed_offline_state_is_migrated(tmp_path: Path):
    notifier = RecordingNotifier()
    state_store = AlertStateStore(tmp_path / "alerts.db")
    state_store.put(RunnerState(
        runner_id="7",
        runner_name="linux-build-01",
        status="offline",
        offline_since=100,
        alert_sent=2,
        last_attempt_at=None,
        labels=["linux", "gpu"],
    ))
    engine = AlertEngine(state_store, notifier)
    asyncio.run(engine.process_snapshot(snapshot("offline"), config(), now=220))
    assert len(notifier.messages) == 1
    assert "已离线" in str(notifier.messages[0]["title"])


def test_dingtalk_signature_matches_documented_algorithm():
    timestamp = 1724472000000
    secret = "SEC-example"
    url = DingTalkNotifier.signed_webhook(
        "https://oapi.dingtalk.com/robot/send?access_token=abc", secret, timestamp
    )
    query = parse_qs(urlparse(url).query)
    expected = base64.b64encode(
        hmac.new(
            secret.encode(), f"{timestamp}\n{secret}".encode(), hashlib.sha256
        ).digest()
    ).decode()
    assert query["access_token"] == ["abc"]
    assert query["timestamp"] == [str(timestamp)]
    assert query["sign"] == [expected]
