"""Example client submitting a test patch and streaming SSE evaluation output."""
import asyncio
import json
import sys
import httpx


SAMPLE_PATCH = """diff --git a/uv/resolver.py b/uv/resolver.py
--- a/uv/resolver.py
+++ b/uv/resolver.py
@@ -10,4 +10,4 @@
 def resolve_bug(x):
-    return False
+    return True
"""


async def run_client_demo(base_url: str = "http://127.0.0.1:8000"):
    payload = {
        "repo_url": "https://github.com/astral-sh/uv",
        "base_commit": "e3a89b1",
        "patch_diff": SAMPLE_PATCH,
        "test_command": "pytest tests/test_resolver.py",
        "base_test_command": "pytest tests/test_resolver.py",
        "timeout_seconds": 30,
        "environment": "python-3.11",
        "metadata": {
            "test_run": "example_patch_01"
        }
    }

    async with httpx.AsyncClient(timeout=60.0) as client:
        print(f"Submitting evaluation to {base_url}/api/v1/eval/run?sync=false")
        submit_res = await client.post(f"{base_url}/api/v1/eval/run?sync=false", json=payload)

        if submit_res.status_code != 200:
            print(f"Submission failed: {submit_res.status_code} - {submit_res.text}")
            return

        init_data = submit_res.json()
        task_id = init_data["task_id"]
        print(f"Task queued with ID: {task_id}\n")

        print(f"Streaming logs from /api/v1/eval/{task_id}/stream:")
        stream_url = f"{base_url}/api/v1/eval/{task_id}/stream"
        async with client.stream("GET", stream_url) as response:
            current_event_type = None
            async for line in response.aiter_lines():
                if not line:
                    continue

                if line.startswith("event: "):
                    current_event_type = line[7:].strip()
                elif line.startswith("data: "):
                    data_str = line[6:].strip()
                    try:
                        event_data = json.loads(data_str)
                        chunk = event_data.get("chunk")
                        status = event_data.get("status")

                        if current_event_type == "status" and chunk:
                            print(f"[STATUS: {status}] {chunk.strip()}")
                        elif current_event_type == "log" and chunk:
                            sys.stdout.write(chunk)
                            sys.stdout.flush()
                        elif current_event_type == "error" and chunk:
                            print(f"[ERROR] {chunk.strip()}")
                        elif current_event_type == "done":
                            print(f"\n[DONE] Execution completed.")
                            break
                    except json.JSONDecodeError:
                        pass

        # Fetch final evaluation report
        print(f"\nFetching final report from /api/v1/eval/{task_id}/status")
        status_res = await client.get(f"{base_url}/api/v1/eval/{task_id}/status")
        final_res = status_res.json()

        print("\n--- Evaluation Summary ---")
        print(f"Task ID:                  {final_res.get('task_id')}")
        print(f"Status:                   {final_res.get('status')}")
        print(f"Resolved:                 {final_res.get('resolved')}")
        print(f"Sandbox Latency:          {final_res.get('sandbox_acquisition_ms', 0):.2f} ms")
        print(f"Execution Latency:        {final_res.get('execution_time_ms', 0):.2f} ms")

        metrics = final_res.get("metrics")
        if metrics:
            print(f"FAIL_TO_PASS:             {metrics.get('fail_to_pass', [])}")
            print(f"PASS_TO_PASS:             {metrics.get('pass_to_pass', [])}")
            print(f"PASS_TO_FAIL:             {metrics.get('pass_to_fail', [])}")
            print(f"FAIL_TO_FAIL:             {metrics.get('fail_to_fail', [])}")
            print(f"Pass Rate:                {metrics.get('pass_rate', 0)}%")
            print(f"Resolution Status:        {metrics.get('resolution_status')}")


if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
    asyncio.run(run_client_demo(url))
