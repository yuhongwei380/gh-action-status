from __future__ import annotations

import hmac
import os
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from starlette.middleware.sessions import SessionMiddleware

from .config import AppConfig, ConfigStore
from .github import GitHubAPIError, RunnerService

BASE_DIR = Path(__file__).resolve().parent


class LoginPayload(BaseModel):
    password: str


class SettingsPayload(BaseModel):
    scope_type: Literal["organization", "repository", "enterprise"]
    scope: str = Field(min_length=1, max_length=200)
    api_url: str = Field(min_length=8, max_length=500)
    token: str | None = Field(default=None, max_length=500)
    refresh_interval: int = Field(default=30, ge=10, le=3600)

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
    app = FastAPI(title="Runner Beacon", docs_url=None, redoc_url=None)
    secret = os.getenv("APP_SECRET", "change-me-before-production")
    store = ConfigStore(data_dir or Path(os.getenv("DATA_DIR", "./data")), secret)
    app.state.store = store
    app.state.runners = RunnerService(store)
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
        }

    @app.put("/api/settings")
    async def update_settings(payload: SettingsPayload, _: bool = Depends(require_auth)) -> dict[str, object]:
        current = store.load()
        token = payload.token.strip() if payload.token else current.token
        if not token:
            raise HTTPException(status_code=422, detail="请填写 GitHub Token")
        config = AppConfig(
            scope_type=payload.scope_type,
            scope=payload.scope,
            api_url=payload.api_url,
            token=token,
            refresh_interval=payload.refresh_interval,
        )
        # Validate repository formatting before persisting.
        RunnerService._endpoint(config)
        store.save(config)
        app.state.runners.invalidate()
        return {"saved": True, "token_hint": f"••••{token[-4:]}"}

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


app = create_app()
