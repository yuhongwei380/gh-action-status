import httpx
import pytest

from app.config import AppConfig
from app.github import GitHubAPIError, RunnerService


def test_endpoint_for_supported_scopes():
    assert RunnerService._endpoint(AppConfig(scope_type="organization", scope="acme")) == "https://api.github.com/orgs/acme/actions/runners"
    assert RunnerService._endpoint(AppConfig(scope_type="repository", scope="acme/api")) == "https://api.github.com/repos/acme/api/actions/runners"
    assert RunnerService._endpoint(AppConfig(scope_type="enterprise", scope="acme")) == "https://api.github.com/enterprises/acme/actions/runners"


def test_api_version_distinguishes_cloud_and_ghes():
    assert RunnerService._api_version("https://api.github.com") == "2026-03-10"
    assert RunnerService._api_version("https://api.acme.ghe.com") == "2026-03-10"
    assert RunnerService._api_version("https://github.acme.test/api/v3") == "2022-11-28"


def test_upstream_unauthorized_is_not_local_session_unauthorized():
    response = httpx.Response(401, request=httpx.Request("GET", "https://api.github.com/orgs/acme/actions/runners"))
    with pytest.raises(GitHubAPIError) as error:
        RunnerService._raise_for_response(response)
    assert error.value.status_code == 424
    assert str(error.value) == "GitHub Token 无效或已过期"


def test_snapshot_aggregates_runner_and_label_status():
    runners = [
        {"id": 1, "name": "linux-1", "os": "linux", "status": "online", "busy": False, "labels": [{"name": "linux"}, {"name": "gpu"}]},
        {"id": 2, "name": "linux-2", "os": "linux", "status": "online", "busy": True, "labels": [{"name": "linux"}]},
        {"id": 3, "name": "gpu-off", "os": "linux", "status": "offline", "busy": False, "labels": [{"name": "gpu"}]},
    ]
    snapshot = RunnerService._build_snapshot(runners, AppConfig(scope="acme"), {"remaining": 100})
    assert snapshot["summary"] == {"total": 3, "online": 2, "idle": 1, "busy": 1, "offline": 1, "utilization": 50}
    gpu = next(label for label in snapshot["labels"] if label["name"] == "gpu")
    assert gpu == {"name": "gpu", "total": 2, "online": 1, "busy": 0, "offline": 1}
