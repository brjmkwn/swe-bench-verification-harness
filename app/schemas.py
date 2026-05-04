from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class EvalStatus(str, Enum):
    QUEUED = "QUEUED"
    PROVISIONING = "PROVISIONING"
    APPLYING_PATCH = "APPLYING_PATCH"
    RUNNING_BASE_TESTS = "RUNNING_BASE_TESTS"
    RUNNING_PATCHED_TESTS = "RUNNING_PATCHED_TESTS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    TIMEOUT = "TIMEOUT"
    REJECTED_PATCH = "REJECTED_PATCH"


class TestStatus(str, Enum):
    __test__ = False
    PASSED = "PASSED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    ERROR = "ERROR"


class TestResultItem(BaseModel):
    __test__ = False
    test_name: str
    status: TestStatus
    duration_ms: Optional[float] = None
    error_message: Optional[str] = None


class SWEBenchMetrics(BaseModel):
    fail_to_pass: List[str] = Field(
        default_factory=list,
        description="Tests that failed before patch but passed after patch (Fixed issues)"
    )
    pass_to_pass: List[str] = Field(
        default_factory=list,
        description="Tests that passed before patch and remained passing (No regressions)"
    )
    fail_to_fail: List[str] = Field(
        default_factory=list,
        description="Tests that failed before patch and continued failing"
    )
    pass_to_fail: List[str] = Field(
        default_factory=list,
        description="Tests that passed before patch but failed after patch (regressions)"
    )
    resolved: bool = Field(
        default=False,
        description="True if target failing tests resolved without regressions"
    )
    pass_rate: float = Field(
        default=0.0,
        description="Percentage of total tracked tests that passed"
    )
    total_tests_evaluated: int = 0
    resolution_status: str = Field(
        default="UNRESOLVED",
        description="Resolution status summary"
    )


class EvalRequest(BaseModel):
    repo_url: str = Field(..., description="Repository Git URI or local path")
    base_commit: Optional[str] = Field(default=None, description="Base Git commit hash to checkout before patch")
    patch_diff: str = Field(..., description="Unified git patch diff to apply and evaluate")
    test_command: str = Field(..., description="Test command to run (e.g. pytest tests/test_resolver.py)")
    base_test_command: Optional[str] = Field(
        default=None,
        description="Optional command to capture baseline test results prior to patch"
    )
    timeout_seconds: int = Field(default=60, ge=1, le=600, description="Max execution timeout in seconds")
    environment: str = Field(default="python-3.11", description="Target runtime environment (e.g. python-3.11, node-20)")
    task_id: Optional[str] = Field(default=None, description="Optional custom evaluation task ID")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Arbitrary task metadata")


class EvalResponse(BaseModel):
    task_id: str
    status: EvalStatus
    resolved: bool = False
    metrics: Optional[SWEBenchMetrics] = None
    stdout: str = ""
    stderr: str = ""
    exit_code: Optional[int] = None
    execution_time_ms: float = 0.0
    sandbox_acquisition_ms: float = 0.0
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: Optional[datetime] = None
    error: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class EvalStreamEvent(BaseModel):
    task_id: str
    event_type: str = Field(..., description="Event type: 'log', 'status', 'metric', 'error', 'done'")
    timestamp: float = Field(default_factory=lambda: datetime.now(timezone.utc).timestamp())
    chunk: Optional[str] = None
    status: Optional[EvalStatus] = None
    payload: Optional[Dict[str, Any]] = None


class PoolHealthResponse(BaseModel):
    status: str
    available_sandboxes: Dict[str, int]
    active_sandboxes: int
    pool_min_idle: int
    pool_max_idle: int
    docker_connected: bool
    mock_mode: bool
