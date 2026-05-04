"""Concurrent load test script for SWE-bench verification harness."""
import asyncio
import json
import statistics
import time
from pathlib import Path
from typing import Any, Dict, List
import httpx


async def run_single_eval(client: httpx.AsyncClient, base_url: str, task: Dict[str, Any], task_index: int) -> Dict[str, Any]:
    payload = {
        "repo_url": task.get("repo_url", "https://github.com/astral-sh/uv"),
        "base_commit": task.get("base_commit", "e3a89b1"),
        "patch_diff": task.get("patch_diff", "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-1\n+2\n"),
        "test_command": task.get("test_command", "pytest tests/test_resolver.py"),
        "timeout_seconds": task.get("timeout_seconds", 30),
        "environment": task.get("environment", "python-3.11"),
        "task_id": f"benchmark_task_{task_index:04d}",
        "metadata": {"load_test": True, "worker_id": task_index}
    }

    start = time.time()
    try:
        resp = await client.post(f"{base_url}/api/v1/eval/run?sync=true", json=payload, timeout=65.0)
        duration_ms = (time.time() - start) * 1000
        if resp.status_code == 200:
            data = resp.json()
            return {
                "success": True,
                "status_code": resp.status_code,
                "duration_ms": duration_ms,
                "sandbox_acquisition_ms": data.get("sandbox_acquisition_ms", 0.0),
                "resolved": data.get("resolved", False),
                "eval_status": data.get("status", "UNKNOWN"),
            }
        else:
            return {
                "success": False,
                "status_code": resp.status_code,
                "duration_ms": duration_ms,
                "error": resp.text
            }
    except Exception as e:
        duration_ms = (time.time() - start) * 1000
        return {
            "success": False,
            "status_code": 0,
            "duration_ms": duration_ms,
            "error": str(e)
        }


async def run_load_benchmark(base_url: str = "http://127.0.0.1:8000", total_requests: int = 50, concurrency: int = 10):
    tasks_file = Path(__file__).parent / "sample_swe_tasks.json"
    sample_tasks = []
    if tasks_file.exists():
        with open(tasks_file, "r") as f:
            sample_tasks = json.load(f)

    if not sample_tasks:
        sample_tasks = [{}]

    print(f"Running benchmark against {base_url} (requests={total_requests}, concurrency={concurrency})")

    semaphore = asyncio.Semaphore(concurrency)
    limits = httpx.Limits(max_connections=concurrency * 2, max_keepalive_connections=concurrency)

    async with httpx.AsyncClient(limits=limits) as client:
        try:
            health_res = await client.get(f"{base_url}/health", timeout=5.0)
            print(f"Health check: {health_res.json()}")
        except Exception as e:
            print(f"Health check warning: {e}")

        async def _bounded_eval(idx: int):
            task_data = sample_tasks[idx % len(sample_tasks)]
            async with semaphore:
                return await run_single_eval(client, base_url, task_data, idx)

        start_time = time.time()
        tasks = [_bounded_eval(i) for i in range(total_requests)]
        results: List[Dict[str, Any]] = await asyncio.gather(*tasks)
        total_time = time.time() - start_time

    successful = [r for r in results if r.get("success")]
    failed = [r for r in results if not r.get("success")]
    total_durations = [r["duration_ms"] for r in successful]
    acq_latencies = [r.get("sandbox_acquisition_ms", 0.0) for r in successful if r.get("sandbox_acquisition_ms", 0.0) > 0]
    resolved_count = sum(1 for r in successful if r.get("resolved"))

    print("\nBenchmark Results:")
    print(f"  Total Requests:       {total_requests}")
    print(f"  Successful:           {len(successful)} ({(len(successful)/total_requests)*100:.1f}%)")
    print(f"  Failed:               {len(failed)}")
    print(f"  Total Time:           {total_time:.2f}s")
    print(f"  Throughput:           {total_requests / total_time:.2f} req/s")
    print(f"  Resolved Tasks:       {resolved_count} / {len(successful)}")

    if total_durations:
        print("\nExecution Latency (ms):")
        print(f"  Min:  {min(total_durations):.2f}")
        print(f"  P50:  {statistics.median(total_durations):.2f}")
        if len(total_durations) > 1:
            print(f"  P95:  {statistics.quantiles(total_durations, n=20)[18]:.2f}")
            print(f"  P99:  {statistics.quantiles(total_durations, n=100)[98]:.2f}")
        print(f"  Max:  {max(total_durations):.2f}")

    if acq_latencies:
        print("\nSandbox Acquisition Latency (ms):")
        print(f"  Min:  {min(acq_latencies):.2f}")
        print(f"  P50:  {statistics.median(acq_latencies):.2f}")
        print(f"  Mean: {statistics.mean(acq_latencies):.2f}")
        print(f"  Max:  {max(acq_latencies):.2f}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="SWE-bench Verification Harness Load Benchmark")
    parser.add_argument("--url", default="http://127.0.0.1:8000", help="Service Base URL")
    parser.add_argument("--requests", type=int, default=50, help="Total evaluation requests")
    parser.add_argument("--concurrency", type=int, default=10, help="Concurrency limit")
    args = parser.parse_args()

    asyncio.run(run_load_benchmark(base_url=args.url, total_requests=args.requests, concurrency=args.concurrency))
