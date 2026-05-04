"""TestExecutionRunner: Orchestrates test executions, patch application, and SSE log streaming."""
import asyncio
import time
import uuid
from datetime import datetime, timezone
from typing import AsyncGenerator, Dict, Optional
from app.config import settings
from app.core.patcher import GitPatcher
from app.core.scorer import SWEBenchScorer
from app.sandbox.container import DockerContainerInstance
from app.sandbox.pool import pool
from app.schemas import (
    EvalRequest,
    EvalResponse,
    EvalStatus,
    EvalStreamEvent,
    SWEBenchMetrics,
)
from app.utils.logger import logger
from app.utils.opik_tracker import tracker


class TaskExecutionContext:
    """Holds active execution state, result cache, and event pub-sub channel."""

    def __init__(self, task_id: str, request: EvalRequest):
        self.task_id = task_id
        self.request = request
        self.status = EvalStatus.QUEUED
        self.response: Optional[EvalResponse] = None
        self.event_queue: asyncio.Queue[EvalStreamEvent] = asyncio.Queue()
        self.log_buffer: list[str] = []
        self.created_at = datetime.now(timezone.utc)

    async def emit_event(self, event_type: str, chunk: Optional[str] = None, status: Optional[EvalStatus] = None, payload: Optional[dict] = None):
        """Pushes an event to the SSE queue."""
        if status:
            self.status = status
        if chunk:
            self.log_buffer.append(chunk)

        event = EvalStreamEvent(
            task_id=self.task_id,
            event_type=event_type,
            chunk=chunk,
            status=self.status,
            payload=payload
        )
        await self.event_queue.put(event)


class TestExecutionRunner:
    """Orchestrates test execution, patch application, and log streaming."""

    def __init__(self):
        self._tasks: Dict[str, TaskExecutionContext] = {}
        self._lock = asyncio.Lock()

    def get_task(self, task_id: str) -> Optional[TaskExecutionContext]:
        return self._tasks.get(task_id)

    async def run_evaluation(self, request: EvalRequest) -> EvalResponse:
        """Executes full evaluation workflow."""
        task_id = request.task_id or f"eval_{uuid.uuid4().hex[:12]}"
        ctx = TaskExecutionContext(task_id, request)

        async with self._lock:
            self._tasks[task_id] = ctx

        start_time = time.time()
        container: Optional[DockerContainerInstance] = None
        sandbox_acq_ms = 0.0

        base_stdout, base_stderr = "", ""
        base_exit_code = 1
        patched_stdout, patched_stderr = "", ""
        patched_exit_code = 1
        error_msg: Optional[str] = None
        resolved = False
        metrics: Optional[SWEBenchMetrics] = None

        try:
            # 1. Allocate sandbox container
            await ctx.emit_event("status", chunk="Allocating sandbox container...\n", status=EvalStatus.PROVISIONING)
            acq_start = time.time()
            container = await pool.acquire(environment_type=request.environment)
            sandbox_acq_ms = (time.time() - acq_start) * 1000

            await ctx.emit_event("log", chunk=f"Sandbox container {container.short_id} ready in {sandbox_acq_ms:.2f}ms\n")

            # 2. Checkout base commit if provided
            if request.base_commit:
                await ctx.emit_event("log", chunk=f"Checking out base commit {request.base_commit}\n")
                checkout_cmd = f"git checkout -f {request.base_commit}"
                checkout_res = await container.exec_command(checkout_cmd, timeout_seconds=15.0)
                if checkout_res.exit_code != 0:
                    logger.debug(f"Git checkout message: {checkout_res.stderr}")

            # 3. Base test execution
            base_cmd = request.base_test_command or request.test_command
            await ctx.emit_event("status", chunk=f"Running baseline test: {base_cmd}\n", status=EvalStatus.RUNNING_BASE_TESTS)

            async for stream_type, chunk in container.exec_command_stream(base_cmd, timeout_seconds=request.timeout_seconds):
                if stream_type == "stdout":
                    base_stdout += chunk
                else:
                    base_stderr += chunk
                await ctx.emit_event("log", chunk=chunk)

            base_res = await container.exec_command(base_cmd, timeout_seconds=request.timeout_seconds)
            base_exit_code = base_res.exit_code
            await ctx.emit_event("log", chunk=f"\nBaseline run completed (exit code {base_exit_code}).\n")

            # 4. Apply patch
            await ctx.emit_event("status", chunk="Applying patch diff...\n", status=EvalStatus.APPLYING_PATCH)
            patch_applied, patch_status_msg = await GitPatcher.apply_patch_to_container(
                container=container,
                patch_diff=request.patch_diff,
                workspace_dir=settings.SANDBOX_WORKSPACE_DIR
            )

            if not patch_applied:
                error_msg = patch_status_msg
                await ctx.emit_event("error", chunk=f"Patch application failed: {error_msg}\n", status=EvalStatus.REJECTED_PATCH)
                total_time_ms = (time.time() - start_time) * 1000

                response = EvalResponse(
                    task_id=task_id,
                    status=EvalStatus.REJECTED_PATCH,
                    resolved=False,
                    stdout=base_stdout,
                    stderr=f"{base_stderr}\n{error_msg}",
                    exit_code=1,
                    execution_time_ms=total_time_ms,
                    sandbox_acquisition_ms=sandbox_acq_ms,
                    completed_at=datetime.now(timezone.utc),
                    error=error_msg,
                    metadata=request.metadata
                )
                ctx.response = response
                tracker.track_evaluation(response, request.metadata)
                await ctx.emit_event("done", payload=response.model_dump(mode="json"))
                return response

            await ctx.emit_event("log", chunk="Patch applied cleanly.\n")

            # 5. Patched test execution
            await ctx.emit_event("status", chunk=f"Running patched test: {request.test_command}\n", status=EvalStatus.RUNNING_PATCHED_TESTS)

            async for stream_type, chunk in container.exec_command_stream(request.test_command, timeout_seconds=request.timeout_seconds):
                if stream_type == "stdout":
                    patched_stdout += chunk
                else:
                    patched_stderr += chunk
                await ctx.emit_event("log", chunk=chunk)

            patched_res = await container.exec_command(request.test_command, timeout_seconds=request.timeout_seconds)
            patched_exit_code = patched_res.exit_code
            await ctx.emit_event("log", chunk=f"\nPatched test completed (exit code {patched_exit_code}).\n")

            # 6. Score metrics
            await ctx.emit_event("status", chunk="Calculating transition metrics...\n")
            metrics = SWEBenchScorer.compute_metrics(
                base_output=f"{base_stdout}\n{base_stderr}",
                patched_output=f"{patched_stdout}\n{patched_stderr}",
                base_exit_code=base_exit_code,
                patched_exit_code=patched_exit_code
            )
            resolved = metrics.resolved

            final_status = EvalStatus.COMPLETED

        except asyncio.TimeoutError:
            final_status = EvalStatus.TIMEOUT
            error_msg = f"Task execution exceeded {request.timeout_seconds}s limit."
            await ctx.emit_event("error", chunk=f"\n[TIMEOUT] {error_msg}\n", status=final_status)
        except Exception as e:
            final_status = EvalStatus.FAILED
            error_msg = str(e)
            logger.exception(f"Unhandled error in evaluation task {task_id}")
            await ctx.emit_event("error", chunk=f"\n[EXECUTION ERROR] {error_msg}\n", status=final_status)
        finally:
            if container:
                await pool.release(container)

        total_time_ms = (time.time() - start_time) * 1000

        response = EvalResponse(
            task_id=task_id,
            status=final_status,
            resolved=resolved,
            metrics=metrics,
            stdout=patched_stdout or base_stdout,
            stderr=patched_stderr or base_stderr or (error_msg or ""),
            exit_code=patched_exit_code,
            execution_time_ms=total_time_ms,
            sandbox_acquisition_ms=sandbox_acq_ms,
            completed_at=datetime.now(timezone.utc),
            error=error_msg,
            metadata=request.metadata
        )

        ctx.response = response
        tracker.track_evaluation(response, request.metadata)
        await ctx.emit_event("done", payload=response.model_dump(mode="json"))

        logger.info(
            f"Completed evaluation {task_id}: Resolved={resolved} in {total_time_ms:.2f}ms",
            extra={"task_id": task_id, "duration_ms": total_time_ms}
        )
        return response

    async def stream_task_events(self, task_id: str) -> AsyncGenerator[str, None]:
        """Yields Server-Sent Events for live task streaming."""
        ctx = self.get_task(task_id)
        if not ctx:
            yield f"event: error\ndata: {{\"error\": \"Task {task_id} not found\"}}\n\n"
            return

        while True:
            try:
                event = await asyncio.wait_for(ctx.event_queue.get(), timeout=30.0)
                event_json = event.model_dump_json()
                yield f"event: {event.event_type}\ndata: {event_json}\n\n"

                if event.event_type in ("done", "error") and ctx.status in (
                    EvalStatus.COMPLETED,
                    EvalStatus.FAILED,
                    EvalStatus.TIMEOUT,
                    EvalStatus.REJECTED_PATCH,
                ):
                    break
            except asyncio.TimeoutError:
                # Keep-alive heartbeat comment
                yield ": keep-alive ping\n\n"


runner = TestExecutionRunner()
