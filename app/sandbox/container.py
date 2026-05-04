from __future__ import annotations
"""DockerContainerInstance: Manages micro-container lifecycle and command execution."""
import asyncio
import io
import os
import tarfile
import time
from typing import Any, AsyncGenerator, Dict, Optional, Tuple

try:
    import docker
    from docker.errors import DockerException, NotFound, APIError
except ImportError:
    docker = None
    DockerException = Exception
    NotFound = Exception
    APIError = Exception

from app.config import settings
from app.sandbox.security import SecurityPolicy
from app.utils.logger import logger


class ContainerExecutionResult:
    def __init__(self, exit_code: int, stdout: str, stderr: str, duration_ms: float, timed_out: bool = False):
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr
        self.duration_ms = duration_ms
        self.timed_out = timed_out


class DockerContainerInstance:
    """Manages an individual Docker micro-sandbox container."""

    def __init__(self, container_id: str, image: str, environment_type: str, docker_client: Optional[docker.DockerClient] = None):
        self.container_id = container_id
        self.image = image
        self.environment_type = environment_type
        self.client = docker_client
        self.created_at = time.time()
        self.is_busy = False
        self._container_obj = None

    @property
    def short_id(self) -> str:
        return self.container_id[:12] if self.container_id else "unknown"

    async def get_raw_container(self):
        """Retrieves or caches the underlying Docker container object."""
        if not self._container_obj and self.client:
            self._container_obj = await asyncio.to_thread(self.client.containers.get, self.container_id)
        return self._container_obj

    async def write_file(self, target_path: str, content: str) -> bool:
        """Injects a file into the container filesystem using an in-memory tar stream."""
        if not self.client:
            return False

        try:
            tar_stream = io.BytesIO()
            content_bytes = content.encode("utf-8")
            tar_info = tarfile.TarInfo(name=os.path.basename(target_path))
            tar_info.size = len(content_bytes)
            tar_info.mtime = int(time.time())
            tar_info.mode = 0o644

            with tarfile.open(fileobj=tar_stream, mode="w") as tar:
                tar.addfile(tar_info, io.BytesIO(content_bytes))

            tar_stream.seek(0)
            dest_dir = os.path.dirname(target_path) or settings.SANDBOX_WORKSPACE_DIR

            container = await self.get_raw_container()
            await asyncio.to_thread(container.put_archive, dest_dir, tar_stream.getvalue())
            return True
        except Exception as e:
            logger.error(f"Failed to inject file to container {self.short_id}: {e}", extra={"container_id": self.short_id})
            return False

    async def exec_command(
        self,
        command: str,
        timeout_seconds: float = 60.0,
        workdir: Optional[str] = None
    ) -> ContainerExecutionResult:
        """Executes a command inside the container synchronously within a thread with timeout."""
        start_time = time.time()
        workdir = workdir or settings.SANDBOX_WORKSPACE_DIR
        cmd_wrapper = ["/bin/sh", "-c", command]

        try:
            container = await self.get_raw_container()
            
            def _run():
                exec_instance = container.client.api.exec_create(
                    container.id,
                    cmd_wrapper,
                    workdir=workdir,
                    user=settings.SANDBOX_USER,
                    environment={"HOME": settings.SANDBOX_WORKSPACE_DIR}
                )
                output = container.client.api.exec_start(exec_instance["Id"], demux=True)
                inspect = container.client.api.exec_inspect(exec_instance["Id"])
                return inspect.get("ExitCode", 1), output

            exit_code, (stdout_bytes, stderr_bytes) = await asyncio.wait_for(
                asyncio.to_thread(_run),
                timeout=timeout_seconds
            )

            stdout = stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else ""
            stderr = stderr_bytes.decode("utf-8", errors="replace") if stderr_bytes else ""
            duration_ms = (time.time() - start_time) * 1000

            return ContainerExecutionResult(
                exit_code=exit_code,
                stdout=stdout,
                stderr=stderr,
                duration_ms=duration_ms,
                timed_out=False
            )

        except asyncio.TimeoutError:
            duration_ms = (time.time() - start_time) * 1000
            logger.warning(f"Command timed out after {timeout_seconds}s in container {self.short_id}")
            return ContainerExecutionResult(
                exit_code=124,
                stdout="",
                stderr=f"Execution timed out after {timeout_seconds} seconds.",
                duration_ms=duration_ms,
                timed_out=True
            )
        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            logger.error(f"Error executing command in {self.short_id}: {e}")
            return ContainerExecutionResult(
                exit_code=1,
                stdout="",
                stderr=f"Container execution error: {str(e)}",
                duration_ms=duration_ms,
                timed_out=False
            )

    async def exec_command_stream(
        self,
        command: str,
        timeout_seconds: float = 60.0,
        workdir: Optional[str] = None
    ) -> AsyncGenerator[Tuple[str, str], None]:
        """Streams command stdout/stderr chunks asynchronously."""
        workdir = workdir or settings.SANDBOX_WORKSPACE_DIR
        cmd_wrapper = ["/bin/sh", "-c", command]

        try:
            container = await self.get_raw_container()
            
            def _create_and_start():
                exec_id = container.client.api.exec_create(
                    container.id,
                    cmd_wrapper,
                    workdir=workdir,
                    user=settings.SANDBOX_USER,
                    environment={"HOME": settings.SANDBOX_WORKSPACE_DIR}
                )["Id"]
                stream = container.client.api.exec_start(exec_id, stream=True, demux=True)
                return exec_id, stream

            exec_id, stream = await asyncio.to_thread(_create_and_start)

            start_time = time.time()
            for stdout_chunk, stderr_chunk in stream:
                if time.time() - start_time > timeout_seconds:
                    yield ("stderr", f"\n[WATCHDOG TIMEOUT] Exceeded {timeout_seconds}s limit\n")
                    break

                if stdout_chunk:
                    yield ("stdout", stdout_chunk.decode("utf-8", errors="replace"))
                if stderr_chunk:
                    yield ("stderr", stderr_chunk.decode("utf-8", errors="replace"))
                await asyncio.sleep(0.001)

        except Exception as e:
            yield ("stderr", f"\n[STREAM ERROR] {str(e)}\n")

    async def terminate(self, force: bool = True):
        """Kills and removes the container asynchronously."""
        if not self.client:
            return
        try:
            container = await self.get_raw_container()
            if container:
                await asyncio.to_thread(container.remove, force=force, v=True)
                logger.info(f"Terminated micro-container {self.short_id}", extra={"container_id": self.short_id})
        except Exception as e:
            logger.debug(f"Could not remove container {self.short_id}: {e}")
        finally:
            self._container_obj = None


class MockContainerInstance(DockerContainerInstance):
    """Fallback in-memory sandbox for local testing when Docker daemon is not running."""

    def __init__(self, container_id: str, image: str, environment_type: str):
        super().__init__(container_id, image, environment_type, None)
        self.fs: Dict[str, str] = {}

    async def get_raw_container(self):
        return None

    async def write_file(self, target_path: str, content: str) -> bool:
        self.fs[target_path] = content
        return True

    async def exec_command(
        self,
        command: str,
        timeout_seconds: float = 60.0,
        workdir: Optional[str] = None
    ) -> ContainerExecutionResult:
        start_time = time.time()
        await asyncio.sleep(0.05)  # Simulate fast micro-execution
        duration_ms = (time.time() - start_time) * 1000

        # Security check simulation
        if "nc " in command or "curl " in command or "ping " in command or "/dev/tcp" in command:
            return ContainerExecutionResult(
                exit_code=1,
                stdout="",
                stderr="Network is unreachable (network_mode=none)",
                duration_ms=duration_ms
            )

        if ":(){ :|:& };:" in command:
            return ContainerExecutionResult(
                exit_code=1,
                stdout="",
                stderr="bash: fork: retry: Resource temporarily unavailable (pids-limit=100 reached)",
                duration_ms=duration_ms
            )

        if "> /etc/" in command or "touch /etc" in command:
            return ContainerExecutionResult(
                exit_code=1,
                stdout="",
                stderr="touch: cannot touch '/etc/test': Read-only file system",
                duration_ms=duration_ms
            )

        # Git patch application simulation
        if "git apply --check" in command:
            patch_content = self.fs.get(f"{settings.SANDBOX_WORKSPACE_DIR}/patch.diff", "")
            if "INVALID_CONFLICT" in patch_content:
                return ContainerExecutionResult(
                    exit_code=1,
                    stdout="",
                    stderr="error: patch failed: main.py:1\nerror: main.py: patch does not apply",
                    duration_ms=duration_ms
                )
            return ContainerExecutionResult(exit_code=0, stdout="Patch check passed", stderr="", duration_ms=duration_ms)

        if "git apply" in command:
            return ContainerExecutionResult(exit_code=0, stdout="Applied patch cleanly", stderr="", duration_ms=duration_ms)

        # Standard test execution simulation
        if "pytest" in command:
            # Check if patch was applied
            patch_content = self.fs.get(f"{settings.SANDBOX_WORKSPACE_DIR}/patch.diff", "")
            if "def test_fixed" in patch_content or "resolve_bug" in patch_content or "test_resolver" in command:
                stdout = (
                    "============================= test session starts ==============================\n"
                    "rootdir: /workspace\n"
                    "collected 3 items\n\n"
                    "tests/test_resolver.py::test_existing_feature PASSED                     [ 33%]\n"
                    "tests/test_resolver.py::test_resolved_bug PASSED                         [ 66%]\n"
                    "tests/test_resolver.py::test_edge_case PASSED                            [100%]\n\n"
                    "============================== 3 passed in 0.24s ==============================="
                )
                return ContainerExecutionResult(exit_code=0, stdout=stdout, stderr="", duration_ms=duration_ms)
            else:
                stdout = (
                    "============================= test session starts ==============================\n"
                    "rootdir: /workspace\n"
                    "collected 2 items\n\n"
                    "tests/test_resolver.py::test_existing_feature PASSED                     [ 50%]\n"
                    "tests/test_resolver.py::test_resolved_bug FAILED                         [100%]\n\n"
                    "============================== 1 failed, 1 passed in 0.18s =============================="
                )
                return ContainerExecutionResult(exit_code=1, stdout=stdout, stderr="", duration_ms=duration_ms)

        return ContainerExecutionResult(exit_code=0, stdout="OK", stderr="", duration_ms=duration_ms)

    async def exec_command_stream(
        self,
        command: str,
        timeout_seconds: float = 60.0,
        workdir: Optional[str] = None
    ) -> AsyncGenerator[Tuple[str, str], None]:
        res = await self.exec_command(command, timeout_seconds, workdir)
        for line in res.stdout.splitlines(keepends=True):
            yield ("stdout", line)
            await asyncio.sleep(0.01)
        if res.stderr:
            for line in res.stderr.splitlines(keepends=True):
                yield ("stderr", line)
                await asyncio.sleep(0.01)

    async def terminate(self, force: bool = True):
        self.fs.clear()
