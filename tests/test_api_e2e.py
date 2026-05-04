"""End-to-end API integration tests for FastAPI evaluation endpoints & SSE streaming."""
import pytest
from httpx import ASGITransport, AsyncClient
from app.main import app


@pytest.mark.anyio
async def test_health_endpoint():
    """Verifies /health endpoint status and pool capacity report."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "HEALTHY"
        assert "available_sandboxes" in data
        assert data["pool_min_idle"] >= 1


@pytest.mark.anyio
async def test_metrics_endpoint():
    """Verifies /metrics Prometheus telemetry exposition."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.get("/metrics")
        assert response.status_code == 200
        assert "swe_eval_total" in response.text
        assert "swe_pool_idle_containers" in response.text


@pytest.mark.anyio
async def test_run_eval_sync_success():
    """Verifies synchronous evaluation of a git patch diff."""
    payload = {
        "repo_url": "https://github.com/astral-sh/uv",
        "base_commit": "e3a89b1",
        "patch_diff": "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-def resolve_bug(x):\n+def resolve_bug(x):\n",
        "test_command": "pytest tests/test_resolver.py",
        "base_test_command": "pytest tests/test_resolver.py",
        "timeout_seconds": 30,
        "environment": "python-3.11",
        "metadata": {"test": True}
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.post("/api/v1/eval/run?sync=true", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert data["task_id"].startswith("eval_")
        assert data["status"] == "COMPLETED"
        assert "metrics" in data
        assert data["metrics"] is not None
        assert data["execution_time_ms"] > 0


@pytest.mark.anyio
async def test_run_eval_async_queue_and_status():
    """Verifies asynchronous task submission and status polling."""
    payload = {
        "repo_url": "https://github.com/astral-sh/uv",
        "base_commit": "e3a89b1",
        "patch_diff": "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-1\n+2\n",
        "test_command": "pytest tests/test_resolver.py",
        "timeout_seconds": 30,
        "environment": "python-3.11",
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Submit async
        submit_res = await ac.post("/api/v1/eval/run?sync=false", json=payload)
        assert submit_res.status_code == 200
        task_id = submit_res.json()["task_id"]

        # Poll status
        status_res = await ac.get(f"/api/v1/eval/{task_id}/status")
        assert status_res.status_code == 200
        assert status_res.json()["task_id"] == task_id
