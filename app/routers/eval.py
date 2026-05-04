"""Evaluation orchestration API routes."""
import asyncio
import uuid
from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from app.core.runner import runner
from app.schemas import EvalRequest, EvalResponse, EvalStatus

router = APIRouter(prefix="/api/v1/eval", tags=["Evaluation"])


@router.post(
    "/run",
    response_model=EvalResponse,
    status_code=status.HTTP_200_OK,
    summary="Submit patch evaluation",
    description="Allocates a container sandbox, applies the git patch, executes test commands, and computes transition metrics."
)
async def run_evaluation(
    request: EvalRequest,
    background_tasks: BackgroundTasks,
    sync: bool = Query(default=True, description="If true, blocks until complete; if false, runs in background")
):
    """Ingests evaluation task and either executes synchronously or queues in background."""
    if not request.task_id:
        request.task_id = f"eval_{uuid.uuid4().hex[:12]}"

    if sync:
        response = await runner.run_evaluation(request)
        return response
    else:
        # Launch asynchronously in background
        background_tasks.add_task(runner.run_evaluation, request)
        return EvalResponse(
            task_id=request.task_id,
            status=EvalStatus.QUEUED,
            resolved=False,
            stdout="",
            stderr="Task queued for asynchronous evaluation.",
            execution_time_ms=0.0,
            metadata=request.metadata
        )


@router.get(
    "/{task_id}/status",
    response_model=EvalResponse,
    summary="Get evaluation status and SWE-bench score",
    description="Retrieves the current execution status, raw logs, and parsed SWE-bench metrics for a task."
)
async def get_evaluation_status(task_id: str):
    """Fetches task status and result."""
    ctx = runner.get_task(task_id)
    if not ctx:
        raise HTTPException(status_code=404, detail=f"Evaluation task '{task_id}' not found.")

    if ctx.response:
        return ctx.response

    return EvalResponse(
        task_id=task_id,
        status=ctx.status,
        resolved=False,
        stdout="".join(ctx.log_buffer),
        stderr="",
        execution_time_ms=0.0,
        metadata=ctx.request.metadata
    )


@router.get(
    "/{task_id}/stream",
    summary="Stream live stdout/stderr logs via SSE",
    description="Opens a Server-Sent Events (SSE) connection streaming real-time log chunks and status events."
)
async def stream_evaluation_logs(task_id: str):
    """Streams live console output chunks and milestone events to client."""
    ctx = runner.get_task(task_id)
    if not ctx:
        raise HTTPException(status_code=404, detail=f"Evaluation task '{task_id}' not found.")

    return StreamingResponse(
        runner.stream_task_events(task_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )
