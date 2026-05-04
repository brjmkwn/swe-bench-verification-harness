"""Telemetry exporter for Prometheus and Opik/Langfuse evaluation logging."""
import time
from typing import Any, Dict, Optional
from prometheus_client import Counter, Histogram, Gauge
from app.config import settings
from app.schemas import EvalResponse, SWEBenchMetrics
from app.utils.logger import logger

EVAL_TOTAL = Counter(
    "swe_eval_total",
    "Total number of evaluation tasks processed",
    ["environment", "status", "resolved"]
)

EVAL_DURATION = Histogram(
    "swe_eval_duration_seconds",
    "Time taken to execute complete evaluation lifecycle",
    ["environment"],
    buckets=[0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0]
)

SANDBOX_ACQUISITION_LATENCY = Histogram(
    "swe_sandbox_acquisition_latency_seconds",
    "Time taken to acquire or fork a sandbox container",
    ["environment", "pool_hit"],
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0]
)

SWE_METRICS_COUNTER = Counter(
    "swe_test_transitions_total",
    "Total number of SWE-bench test outcome transitions",
    ["transition_type"]  # fail_to_pass, pass_to_pass, fail_to_fail, pass_to_fail
)

POOL_IDLE_CONTAINERS = Gauge(
    "swe_pool_idle_containers",
    "Number of idle pre-warmed containers available in pool",
    ["environment"]
)

POOL_ACTIVE_CONTAINERS = Gauge(
    "swe_pool_active_containers",
    "Number of currently active/busy containers",
    ["environment"]
)


class OpikEvaluationTracker:
    """Handles logging evaluation runs and SWE-bench scorecards to Opik / telemetry."""

    def __init__(self):
        self.enabled = bool(settings.OPIK_API_KEY)
        self.client = None
        if self.enabled:
            try:
                import opik
                self.client = opik.Opik(
                    project_name=settings.OPIK_PROJECT_NAME,
                    workspace=settings.OPIK_WORKSPACE
                )
                logger.info("Opik evaluation client initialized successfully.")
            except ImportError:
                logger.warning("Opik library not installed. Disabling Opik integration.")
                self.enabled = False
            except Exception as e:
                logger.warning(f"Failed to initialize Opik client: {e}")
                self.enabled = False

    def track_evaluation(self, response: EvalResponse, request_metadata: Optional[Dict[str, Any]] = None):
        """Records metrics in Prometheus and optionally exports to Opik."""
        # 1. Prometheus Metric Updates
        env = request_metadata.get("environment", "unknown") if request_metadata else "unknown"
        status = response.status.value
        resolved_str = str(response.resolved).lower()

        EVAL_TOTAL.labels(environment=env, status=status, resolved=resolved_str).inc()
        if response.execution_time_ms > 0:
            EVAL_DURATION.labels(environment=env).observe(response.execution_time_ms / 1000.0)

        if response.sandbox_acquisition_ms > 0:
            pool_hit = "true" if response.sandbox_acquisition_ms < 250.0 else "false"
            SANDBOX_ACQUISITION_LATENCY.labels(environment=env, pool_hit=pool_hit).observe(
                response.sandbox_acquisition_ms / 1000.0
            )

        if response.metrics:
            m: SWEBenchMetrics = response.metrics
            if m.fail_to_pass:
                SWE_METRICS_COUNTER.labels(transition_type="fail_to_pass").inc(len(m.fail_to_pass))
            if m.pass_to_pass:
                SWE_METRICS_COUNTER.labels(transition_type="pass_to_pass").inc(len(m.pass_to_pass))
            if m.fail_to_fail:
                SWE_METRICS_COUNTER.labels(transition_type="fail_to_fail").inc(len(m.fail_to_fail))
            if m.pass_to_fail:
                SWE_METRICS_COUNTER.labels(transition_type="pass_to_fail").inc(len(m.pass_to_fail))

        # 2. Opik Tracing Export
        if self.enabled and self.client:
            try:
                trace_data = {
                    "task_id": response.task_id,
                    "status": status,
                    "resolved": response.resolved,
                    "execution_time_ms": response.execution_time_ms,
                    "sandbox_acquisition_ms": response.sandbox_acquisition_ms,
                    "metrics": response.metrics.model_dump() if response.metrics else None,
                    "metadata": response.metadata,
                }
                # Log to Opik project trace
                trace = self.client.trace(
                    name="swe_bench_verification",
                    input={"metadata": request_metadata, "task_id": response.task_id},
                    output=trace_data,
                    tags=["swe-bench", env, "resolved" if response.resolved else "unresolved"],
                    metadata={
                        "resolution_status": response.metrics.resolution_status if response.metrics else "UNKNOWN",
                        "pass_rate": response.metrics.pass_rate if response.metrics else 0.0,
                    }
                )
                trace.end()
            except Exception as e:
                logger.warning(f"Error exporting trace to Opik: {e}", extra={"task_id": response.task_id})


tracker = OpikEvaluationTracker()
