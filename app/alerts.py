from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import sqlite3
import time
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import httpx

from .config import AppConfig, ConfigStore
from .github import GitHubAPIError, RunnerService

LOGGER = logging.getLogger(__name__)
CHINA_TIME = timezone(timedelta(hours=8))


class NotificationError(RuntimeError):
    pass


@dataclass(slots=True)
class RunnerState:
    runner_id: str
    runner_name: str
    status: str
    offline_since: float | None
    alert_sent: int
    last_attempt_at: float | None
    labels: list[str]


class AlertStateStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS runner_state (
                runner_id TEXT PRIMARY KEY,
                runner_name TEXT NOT NULL,
                status TEXT NOT NULL,
                offline_since REAL,
                alert_sent INTEGER NOT NULL DEFAULT 0,
                last_attempt_at REAL,
                labels_json TEXT NOT NULL DEFAULT '[]'
            )
            """
        )
        return connection

    def get(self, runner_id: str) -> RunnerState | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM runner_state WHERE runner_id = ?", (runner_id,)
            ).fetchone()
        if row is None:
            return None
        return RunnerState(
            runner_id=row["runner_id"],
            runner_name=row["runner_name"],
            status=row["status"],
            offline_since=row["offline_since"],
            alert_sent=row["alert_sent"],
            last_attempt_at=row["last_attempt_at"],
            labels=json.loads(row["labels_json"]),
        )

    def put(self, state: RunnerState) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO runner_state
                    (runner_id, runner_name, status, offline_since, alert_sent, last_attempt_at, labels_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(runner_id) DO UPDATE SET
                    runner_name = excluded.runner_name,
                    status = excluded.status,
                    offline_since = excluded.offline_since,
                    alert_sent = excluded.alert_sent,
                    last_attempt_at = excluded.last_attempt_at,
                    labels_json = excluded.labels_json
                """,
                (
                    state.runner_id,
                    state.runner_name,
                    state.status,
                    state.offline_since,
                    state.alert_sent,
                    state.last_attempt_at,
                    json.dumps(state.labels, ensure_ascii=False),
                ),
            )

    def reset(self) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM runner_state")


class Notifier(Protocol):
    async def send(
        self, settings: dict[str, Any], title: str, text: str, mentions: list[str]
    ) -> None: ...


class DingTalkNotifier:
    @staticmethod
    def signed_webhook(webhook: str, secret: str, timestamp_ms: int) -> str:
        signature = hmac.new(
            secret.encode("utf-8"),
            f"{timestamp_ms}\n{secret}".encode("utf-8"),
            digestmod=hashlib.sha256,
        ).digest()
        sign = base64.b64encode(signature).decode("ascii")
        parsed = urlparse(webhook)
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        query.update({"timestamp": str(timestamp_ms), "sign": sign})
        return urlunparse(parsed._replace(query=urlencode(query)))

    async def send(
        self, settings: dict[str, Any], title: str, text: str, mentions: list[str]
    ) -> None:
        webhook = str(settings.get("webhook", ""))
        secret = str(settings.get("secret", ""))
        url = self.signed_webhook(webhook, secret, int(time.time() * 1000)) if secret else webhook
        payload = {
            "msgtype": "markdown",
            "markdown": {"title": title, "text": text},
            "at": {"atMobiles": mentions, "isAtAll": False},
        }
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(10.0, connect=5.0), follow_redirects=False
            ) as client:
                response = await client.post(url, json=payload)
                response.raise_for_status()
                result = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise NotificationError("无法连接钉钉机器人") from exc
        if result.get("errcode") != 0:
            raise NotificationError(f"钉钉机器人返回错误：{result.get('errmsg', '未知错误')}")


class AlertEngine:
    def __init__(self, state_store: AlertStateStore, notifier: Notifier) -> None:
        self.state_store = state_store
        self.notifier = notifier

    @staticmethod
    def resolve_mentions(
        runner_name: str, labels: list[str], settings: dict[str, Any]
    ) -> list[str]:
        routes = settings.get("routes", [])
        runner_mentions: list[str] = []
        label_mentions: list[str] = []
        label_set = set(labels)
        for route in routes if isinstance(routes, list) else []:
            if not isinstance(route, dict):
                continue
            match_type = route.get("match_type")
            match_value = route.get("match_value")
            mentions = route.get("mentions", [])
            if match_type == "runner" and match_value == runner_name:
                runner_mentions.extend(mentions)
            elif match_type == "label" and match_value in label_set:
                label_mentions.extend(mentions)
        selected = runner_mentions or label_mentions or settings.get("default_mentions", [])
        return list(dict.fromkeys(str(item) for item in selected if item))

    async def send_test(self, config: AppConfig) -> None:
        settings = config.alerts
        mentions = [str(item) for item in settings.get("default_mentions", [])]
        mention_text = " ".join(f"@{mobile}" for mobile in mentions)
        text = "### Runner Beacon 测试通知\n\n钉钉离线告警配置有效。"
        if mention_text:
            text += f"\n\n{mention_text}"
        await self.notifier.send(settings, "Runner Beacon 测试通知", text, mentions)

    async def process_snapshot(
        self, snapshot: dict[str, Any], config: AppConfig, now: float | None = None
    ) -> None:
        current_time = time.time() if now is None else now
        threshold = int(config.alerts.get("offline_after", 120))
        for runner in snapshot.get("runners", []):
            runner_id = str(runner.get("id"))
            name = str(runner.get("name", "未命名 Runner"))
            status = str(runner.get("status", "offline"))
            labels = [str(label) for label in runner.get("labels", [])]
            previous = self.state_store.get(runner_id)

            if previous is None:
                self.state_store.put(
                    RunnerState(
                        runner_id, name, status,
                        current_time if status == "offline" else None,
                        0,
                        None, labels,
                    )
                )
                if status == "offline":
                    LOGGER.info(
                        "Runner %s first observed offline; alert timer started", name
                    )
                continue

            if status == "online":
                if previous.status == "offline" and previous.alert_sent == 1:
                    if bool(config.alerts.get("recovery_enabled", True)):
                        await self._notify(config, name, labels, recovered=True, now=current_time)
                        LOGGER.info("Runner recovery notification sent: %s", name)
                self.state_store.put(RunnerState(runner_id, name, status, None, 0, None, labels))
                continue

            if previous.status == "online":
                self.state_store.put(
                    RunnerState(runner_id, name, status, current_time, 0, None, labels)
                )
                LOGGER.info("Runner transitioned offline; alert timer started: %s", name)
                continue

            offline_since = (
                previous.offline_since
                if previous.offline_since is not None
                else current_time
            )
            # Version 1 used 2 to permanently suppress an initially offline runner.
            # Normalize persisted legacy rows so upgrades begin a normal alert timer.
            alert_sent = 0 if previous.alert_sent == 2 else previous.alert_sent
            retry_ready = (
                previous.last_attempt_at is None or current_time - previous.last_attempt_at >= 300
            )
            if alert_sent == 0 and current_time - offline_since >= threshold and retry_ready:
                try:
                    await self._notify(config, name, labels, recovered=False, now=current_time)
                except NotificationError:
                    self.state_store.put(
                        RunnerState(runner_id, name, status, offline_since, 0, current_time, labels)
                    )
                    raise
                self.state_store.put(
                    RunnerState(runner_id, name, status, offline_since, 1, current_time, labels)
                )
                LOGGER.info("Runner offline notification sent: %s", name)
            else:
                self.state_store.put(
                    RunnerState(
                        runner_id, name, status, offline_since,
                        alert_sent, previous.last_attempt_at, labels,
                    )
                )

    async def _notify(
        self, config: AppConfig, name: str, labels: list[str], recovered: bool, now: float
    ) -> None:
        mentions = self.resolve_mentions(name, labels, config.alerts)
        label_text = ", ".join(labels) if labels else "无"
        status_text = "已恢复在线" if recovered else "已离线"
        title = f"Runner {status_text}：{name}"
        text = (
            f"### {title}\n\n"
            f"- **Runner：** {name}\n"
            f"- **Labels：** {label_text}\n"
            f"- **作用域：** {config.scope}\n"
            f"- **时间：** {datetime.fromtimestamp(now, CHINA_TIME).strftime('%Y-%m-%d %H:%M:%S')} (UTC+8)"
        )
        if mentions:
            text += "\n\n" + " ".join(f"@{mobile}" for mobile in mentions)
        await self.notifier.send(config.alerts, title, text, mentions)


async def monitor_loop(
    store: ConfigStore,
    runners: RunnerService,
    engine: AlertEngine,
    wake_event: asyncio.Event,
) -> None:
    while True:
        config = store.load()
        alerts = config.alerts
        if alerts.get("enabled") and alerts.get("webhook") and config.token and config.scope:
            try:
                snapshot = await runners.snapshot(force=True)
                await engine.process_snapshot(snapshot, config)
            except (GitHubAPIError, NotificationError, OSError, sqlite3.Error) as exc:
                LOGGER.warning("Runner alert monitor cycle failed: %s", exc)
        # A short offline threshold must also shorten the monitor interval;
        # otherwise a 10-second setting could still wait for a 30-second refresh.
        offline_after = int(alerts.get("offline_after", 120))
        wait_seconds = max(10, min(int(config.refresh_interval), offline_after))
        try:
            await asyncio.wait_for(wake_event.wait(), timeout=wait_seconds)
            wake_event.clear()
        except asyncio.TimeoutError:
            pass
        except asyncio.CancelledError:
            with suppress(Exception):
                wake_event.clear()
            raise
