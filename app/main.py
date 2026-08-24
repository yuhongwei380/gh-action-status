from __future__ import annotations

import asyncio
import hmac
import os
import re
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from starlette.middleware.sessions import SessionMiddleware

from .alerts import AlertEngine, AlertStateStore, DingTalkNotifier, NotificationError, monitor_loop
from .config import AppConfig, ConfigStore, default_alert_settings
from .github import GitHubAPIError, RunnerService

BASE_DIR = Path(__file__).resolve().parent


class LoginPayload(BaseModel):
    password: str


class AlertRoutePayload(BaseModel):
    match_type: Literal["runner", "label"]
    match_value: str = Field(min_length=1, max_length=200)
    mentions: list[str] = Field(default_factory=list, max_length=50)

    @field_validator("match_value")
    @classmethod
    def strip_match_value(cls, value: str) -> str:
        return value.strip()

    @field_validator("mentions")
    @classmethod
    def validate_mentions(cls, values: list[str]) -> list[str]:
        cleaned: list[str] = []
        for value in values:
            mobile = value.strip()
            if not re.fullmatch(r"\+?\d{6,20}", mobile):
                raise ValueError("@手机号格式不正确")
            if mobile not in cleaned:
                cleaned.append(mobile)
        return cleaned


class AlertSettingsPayload(BaseModel):
    enabled: bool = False
    webhook: str | None = Field(default=None, max_length=1000)
    secret: str | None = Field(default=None, max_length=500)
    offline_after: int = Field(default=120, ge=10, le=3600)
    recovery_enabled: bool = True
    default_mentions: list[str] = Field(default_factory=list, max_length=50)
    routes: list[AlertRoutePayload] = Field(default_factory=list, max_length=100)

    @field_validator("default_mentions")
    @classmethod
    def validate_default_mentions(cls, values: list[str]) -> list[str]:
        return AlertRoutePayload.validate_mentions(values)

    @field_validator("webhook")
    @classmethod
    def validate_webhook(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        webhook = value.strip()
        parsed = urlparse(webhook)
        hostname = (parsed.hostname or "").lower()
        if parsed.scheme != "https" or not (
            hostname == "dingtalk.com" or hostname.endswith(".dingtalk.com")
        ):
            raise ValueError("Webhook 必须是钉钉官方 HTTPS 地址")
        return webhook


class SettingsPayload(BaseModel):
    scope_type: Literal["organization", "repository", "enterprise"]
    scope: str = Field(min_length=1, max_length=200)
    api_url: str = Field(min_length=8, max_length=500)
    token: str | None = Field(default=None, max_length=500)
    refresh_interval: int = Field(default=30, ge=10, le=3600)
    alerts: AlertSettingsPayload | None = None

    @field_validator("scope", "api_url")
    @classmethod
    def strip_values(cls, value: str) -> str:
        return value.strip()

    @field_validator("api_url")
    @classmethod
    def validate_api_url(cls, value: str) -> str:
        if not value.startswith("https://") and not (
            os.getenv("ALLOW_HTTP_API", "false").lower() == "true" and value.startswith("http://")
        ):
            raise ValueError("API 地址必须使用 HTTPS")
        return value.rstrip("/")


def create_app(data_dir: Path | None = None) -> FastAPI:
    secret = os.getenv("APP_SECRET", "change-me-before-production")
    resolved_data_dir = data_dir or Path(os.getenv("DATA_DIR", "./data"))
    store = ConfigStore(resolved_data_dir, secret)
    runner_service = RunnerService(store)
    alert_state = AlertStateStore(resolved_data_dir / "alerts.db")
    alert_engine = AlertEngine(alert_state, DingTalkNotifier())
    monitor_wake = asyncio.Event()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        task = asyncio.create_task(
            monitor_loop(store, runner_service, alert_engine, monitor_wake),
            name="runner-alert-monitor",
        )
        try:
            yield
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    app = FastAPI(title="Runner Beacon", docs_url=None, redoc_url=None, lifespan=lifespan)
    app.state.store = store
    app.state.runners = runner_service
    app.state.alert_engine = alert_engine
    app.state.alert_state = alert_state
    app.state.monitor_wake = monitor_wake
    app.state.admin_password = os.getenv("ADMIN_PASSWORD", "admin")
    app.state.insecure_defaults = secret == "change-me-before-production" or app.state.admin_password == "admin"

    app.add_middleware(
        SessionMiddleware,
        secret_key=secret,
        session_cookie="runner_beacon_session",
        same_site="strict",
        https_only=os.getenv("COOKIE_SECURE", "false").lower() == "true",
        max_age=60 * 60 * 12,
    )

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; style-src 'self'; script-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'"
        )
        if request.url.path == "/" or request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-cache, must-revalidate"
        return response

    app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(BASE_DIR / "static" / "index.html")

    @app.get("/healthz")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/session")
    async def session(request: Request) -> dict[str, bool]:
        return {
            "authenticated": bool(request.session.get("authenticated")),
            "insecure_defaults": app.state.insecure_defaults,
        }

    @app.post("/api/login")
    async def login(payload: LoginPayload, request: Request) -> dict[str, bool]:
        if not hmac.compare_digest(payload.password, app.state.admin_password):
            raise HTTPException(status_code=401, detail="密码错误")
        request.session.clear()
        request.session["authenticated"] = True
        return {"authenticated": True}

    @app.post("/api/logout")
    async def logout(request: Request) -> dict[str, bool]:
        request.session.clear()
        return {"authenticated": False}

    def require_auth(request: Request) -> bool:
        if not request.session.get("authenticated"):
            raise HTTPException(status_code=401, detail="请先登录")
        return True

    @app.get("/api/settings")
    async def get_settings(_: bool = Depends(require_auth)) -> dict[str, object]:
        config = store.load()
        return {
            "scope_type": config.scope_type,
            "scope": config.scope,
            "api_url": config.api_url,
            "refresh_interval": config.refresh_interval,
            "has_token": bool(config.token),
            "token_hint": f"••••{config.token[-4:]}" if len(config.token) >= 4 else ("••••" if config.token else ""),
            "alerts": {
                "enabled": bool(config.alerts.get("enabled")),
                "has_webhook": bool(config.alerts.get("webhook")),
                "webhook_hint": _webhook_hint(str(config.alerts.get("webhook", ""))),
                "has_secret": bool(config.alerts.get("secret")),
                "offline_after": int(config.alerts.get("offline_after", 120)),
                "recovery_enabled": bool(config.alerts.get("recovery_enabled", True)),
                "default_mentions": config.alerts.get("default_mentions", []),
                "routes": config.alerts.get("routes", []),
            },
        }

    @app.put("/api/settings")
    async def update_settings(payload: SettingsPayload, _: bool = Depends(require_auth)) -> dict[str, object]:
        current = store.load()
        token = payload.token.strip() if payload.token else current.token
        if not token:
            raise HTTPException(status_code=422, detail="请填写 GitHub Token")
        alerts = dict(current.alerts or default_alert_settings())
        if payload.alerts is not None:
            incoming = payload.alerts.model_dump()
            incoming["webhook"] = incoming["webhook"] or alerts.get("webhook", "")
            incoming["secret"] = incoming["secret"] or alerts.get("secret", "")
            alerts = incoming
        if alerts.get("enabled") and not alerts.get("webhook"):
            raise HTTPException(status_code=422, detail="启用告警前请填写钉钉 Webhook")
        config = AppConfig(
            scope_type=payload.scope_type,
            scope=payload.scope,
            api_url=payload.api_url,
            token=token,
            refresh_interval=payload.refresh_interval,
            alerts=alerts,
        )
        # Validate repository formatting before persisting.
        RunnerService._endpoint(config)
        store.save(config)
        app.state.runners.invalidate()
        app.state.alert_state.reset()
        app.state.monitor_wake.set()
        return {"saved": True, "token_hint": f"••••{token[-4:]}"}

    @app.post("/api/alerts/test")
    async def test_alert(_: bool = Depends(require_auth)) -> dict[str, bool]:
        config = store.load()
        if not config.alerts.get("webhook"):
            raise HTTPException(status_code=422, detail="请先保存钉钉 Webhook")
        try:
            await app.state.alert_engine.send_test(config)
        except NotificationError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return {"sent": True}

    @app.get("/api/runners")
    async def runners(request: Request, force: bool = False) -> dict[str, object]:
        # Status data is intentionally public. Only authenticated administrators
        # may bypass the cache, preventing anonymous clients from exhausting the
        # upstream GitHub API rate limit.
        is_admin = bool(request.session.get("authenticated"))
        return await app.state.runners.snapshot(force=force and is_admin)

    @app.exception_handler(GitHubAPIError)
    async def github_error(_: Request, exc: GitHubAPIError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": str(exc), "code": "github_api_error"},
        )

    return app


def _webhook_hint(webhook: str) -> str:
    if not webhook:
        return ""
    parsed = urlparse(webhook)
    access_token = dict(item.split("=", 1) for item in parsed.query.split("&") if "=" in item).get(
        "access_token", ""
    )
    suffix = access_token[-4:] if access_token else ""
    return f"{parsed.hostname or 'dingtalk.com'} / ••••{suffix}" if suffix else (parsed.hostname or "已配置")


app = create_app()
