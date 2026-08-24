from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote
from urllib.parse import urlparse

import httpx

from .config import AppConfig, ConfigStore


class GitHubAPIError(RuntimeError):
    def __init__(self, message: str, status_code: int = 502) -> None:
        super().__init__(message)
        self.status_code = status_code


class RunnerService:
    def __init__(self, store: ConfigStore) -> None:
        self.store = store
        self._cache: dict[str, Any] | None = None
        self._cache_key = ""
        self._lock = asyncio.Lock()

    def invalidate(self) -> None:
        self._cache = None
        self._cache_key = ""

    async def snapshot(self, force: bool = False) -> dict[str, Any]:
        config = self.store.load()
        if not config.token or not config.scope:
            raise GitHubAPIError("请先完成 GitHub 连接配置", 409)

        cache_key = f"{config.api_url}|{config.scope_type}|{config.scope}"
        if not force and self._cache and self._cache_key == cache_key:
            fetched = datetime.fromisoformat(self._cache["fetched_at"])
            age = (datetime.now(timezone.utc) - fetched).total_seconds()
            if age < config.refresh_interval:
                return {**self._cache, "cached": True}

        async with self._lock:
            if not force and self._cache and self._cache_key == cache_key:
                fetched = datetime.fromisoformat(self._cache["fetched_at"])
                if (datetime.now(timezone.utc) - fetched).total_seconds() < config.refresh_interval:
                    return {**self._cache, "cached": True}
            runners, rate_limit = await self._fetch_all(config)
            snapshot = self._build_snapshot(runners, config, rate_limit)
            self._cache = snapshot
            self._cache_key = cache_key
            return snapshot

    async def _fetch_all(self, config: AppConfig) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        endpoint = self._endpoint(config)
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {config.token}",
            "X-GitHub-Api-Version": self._api_version(config.api_url),
            "User-Agent": "runner-beacon/1.0",
        }
        runners: list[dict[str, Any]] = []
        rate_limit: dict[str, Any] = {}

        try:
            timeout = httpx.Timeout(12.0, connect=5.0)
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
                for page in range(1, 101):
                    response = await client.get(
                        endpoint, headers=headers, params={"per_page": 100, "page": page}
                    )
                    rate_limit = {
                        "remaining": _to_int(response.headers.get("x-ratelimit-remaining")),
                        "limit": _to_int(response.headers.get("x-ratelimit-limit")),
                        "reset": _to_int(response.headers.get("x-ratelimit-reset")),
                    }
                    if response.status_code >= 400:
                        self._raise_for_response(response)
                    batch = response.json().get("runners", [])
                    runners.extend(batch)
                    if len(batch) < 100:
                        break
        except httpx.TimeoutException as exc:
            raise GitHubAPIError("连接 GitHub 超时，请检查网络或 API 地址", 504) from exc
        except httpx.RequestError as exc:
            raise GitHubAPIError("无法连接 GitHub，请检查网络或 API 地址", 502) from exc
        return runners, rate_limit

    @staticmethod
    def _endpoint(config: AppConfig) -> str:
        base = config.api_url.rstrip("/")
        scope = config.scope.strip().strip("/")
        if config.scope_type == "organization":
            return f"{base}/orgs/{quote(scope, safe='')}/actions/runners"
        if config.scope_type == "enterprise":
            return f"{base}/enterprises/{quote(scope, safe='')}/actions/runners"
        if config.scope_type == "repository":
            parts = scope.split("/")
            if len(parts) != 2 or not all(parts):
                raise GitHubAPIError("仓库作用域格式应为 owner/repository", 422)
            owner, repo = (quote(part, safe="") for part in parts)
            return f"{base}/repos/{owner}/{repo}/actions/runners"
        raise GitHubAPIError("不支持的作用域类型", 422)

    @staticmethod
    def _api_version(api_url: str) -> str:
        """Use the current cloud API while retaining broad GHES compatibility."""
        hostname = (urlparse(api_url).hostname or "").lower()
        return "2026-03-10" if hostname == "api.github.com" or hostname.endswith(".ghe.com") else "2022-11-28"

    @staticmethod
    def _raise_for_response(response: httpx.Response) -> None:
        messages = {
            401: "GitHub Token 无效或已过期",
            403: "Token 权限不足或 API 速率已达上限",
            404: "找不到该作用域，或 Token 无权访问",
        }
        message = messages.get(response.status_code, f"GitHub API 返回错误（{response.status_code}）")
        # Do not propagate an upstream 401 as our own authentication failure:
        # the browser uses local 401 responses to detect an expired admin session.
        status_code = 424 if response.status_code == 401 else (response.status_code if response.status_code < 500 else 502)
        raise GitHubAPIError(message, status_code)

    @staticmethod
    def _build_snapshot(
        raw_runners: list[dict[str, Any]], config: AppConfig, rate_limit: dict[str, Any]
    ) -> dict[str, Any]:
        runners: list[dict[str, Any]] = []
        labels: dict[str, dict[str, Any]] = {}
        for item in raw_runners:
            status = "online" if item.get("status") == "online" else "offline"
            busy = bool(item.get("busy")) and status == "online"
            item_labels = [label.get("name", "") for label in item.get("labels", []) if label.get("name")]
            runner = {
                "id": item.get("id"),
                "name": item.get("name", "未命名 Runner"),
                "os": item.get("os", "unknown"),
                "status": status,
                "busy": busy,
                "labels": item_labels,
            }
            runners.append(runner)
            for name in item_labels:
                stat = labels.setdefault(name, {"name": name, "total": 0, "online": 0, "busy": 0, "offline": 0})
                stat["total"] += 1
                stat[status] += 1
                if busy:
                    stat["busy"] += 1

        runners.sort(key=lambda runner: (runner["status"] == "offline", not runner["busy"], runner["name"].lower()))
        label_list = sorted(labels.values(), key=lambda label: (-label["total"], label["name"].lower()))
        total = len(runners)
        online = sum(runner["status"] == "online" for runner in runners)
        busy = sum(runner["busy"] for runner in runners)
        return {
            "scope": {"type": config.scope_type, "name": config.scope},
            "summary": {
                "total": total,
                "online": online,
                "idle": online - busy,
                "busy": busy,
                "offline": total - online,
                "utilization": round(busy / online * 100) if online else 0,
            },
            "labels": label_list,
            "runners": runners,
            "rate_limit": rate_limit,
            "refresh_interval": config.refresh_interval,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "cached": False,
        }


def _to_int(value: str | None) -> int | None:
    try:
        return int(value) if value is not None else None
    except ValueError:
        return None
