from __future__ import annotations
"""WarmContainerPool: Pre-forked sandbox pool lifecycle manager for sub-second recycling."""
import asyncio
import time
import uuid
from typing import Dict, Optional, Set

try:
    import docker
    from docker.errors import DockerException
except ImportError:
    docker = None
    DockerException = Exception

from app.config import settings
from app.sandbox.container import DockerContainerInstance, MockContainerInstance
from app.sandbox.security import SecurityPolicy
from app.utils.logger import logger
from app.utils.opik_tracker import POOL_ACTIVE_CONTAINERS, POOL_IDLE_CONTAINERS


class WarmContainerPool:
    """Maintains pre-forked, pre-warmed idle micro-containers across execution environments."""

    def __init__(self):
        self._docker_client: Optional[docker.DockerClient] = None
        self._idle_pools: Dict[str, asyncio.Queue] = {
            "python-3.11": asyncio.Queue(),
            "node-20": asyncio.Queue(),
        }
        self._active_containers: Set[DockerContainerInstance] = set()
        self._replenish_tasks: Dict[str, asyncio.Task] = {}
        self._is_running = False
        self._mock_mode = settings.USE_MOCK_DOCKER
        self._lock = asyncio.Lock()

    @property
    def mock_mode(self) -> bool:
        return self._mock_mode

    @property
    def docker_connected(self) -> bool:
        return self._docker_client is not None and not self._mock_mode

    async def initialize(self):
        """Initializes Docker client connection and spawns the warm container pool."""
        self._is_running = True

        if docker is None:
            self._mock_mode = True

        if not self._mock_mode and docker is not None:
            try:
                if settings.DOCKER_BASE_URL:
                    self._docker_client = docker.DockerClient(base_url=settings.DOCKER_BASE_URL)
                else:
                    self._docker_client = docker.from_env()

                # Test docker connection
                await asyncio.to_thread(self._docker_client.ping)
                logger.info("Successfully connected to Docker Daemon.")
            except Exception as e:
                logger.warning(
                    f"Unable to connect to Docker daemon ({e}). Falling back to Mock Sandbox mode for testing/CI."
                )
                self._mock_mode = True

        # Launch background replenishment loops for each runtime
        for env_type in self._idle_pools.keys():
            self._replenish_tasks[env_type] = asyncio.create_task(
                self._replenish_worker(env_type),
                name=f"pool-replenish-{env_type}"
            )

        # Pre-seed pools
        for env_type in self._idle_pools.keys():
            await self._replenish_pool(env_type)

        logger.info(f"WarmContainerPool initialized (Mock Mode: {self._mock_mode}).")

    async def _create_container(self, environment_type: str) -> DockerContainerInstance:
        """Spins up a single, hardened micro-container instance."""
        image_name = (
            settings.SANDBOX_IMAGE_PYTHON if "python" in environment_type else settings.SANDBOX_IMAGE_NODE
        )

        if self._mock_mode:
            mock_id = f"mock_{environment_type}_{uuid.uuid4().hex[:8]}"
            return MockContainerInstance(mock_id, image_name, environment_type)

        def _sync_create():
            host_config = self._docker_client.api.create_host_config(
                **SecurityPolicy.get_container_host_config()
            )
            create_kwargs = SecurityPolicy.get_container_create_kwargs(image_name)

            container_dict = self._docker_client.api.create_container(
                image=create_kwargs["image"],
                command=create_kwargs["command"],
                working_dir=create_kwargs["working_dir"],
                user=create_kwargs["user"],
                environment=create_kwargs["environment"],
                host_config=host_config,
                detach=True,
                name=f"swe_sandbox_{uuid.uuid4().hex[:8]}"
            )
            cid = container_dict["Id"]
            self._docker_client.api.start(cid)
            return cid

        try:
            container_id = await asyncio.wait_for(
                asyncio.to_thread(_sync_create),
                timeout=settings.CONTAINER_STARTUP_TIMEOUT_SECONDS
            )
            instance = DockerContainerInstance(
                container_id=container_id,
                image=image_name,
                environment_type=environment_type,
                docker_client=self._docker_client
            )
            logger.debug(f"Spawned warm container {instance.short_id} ({environment_type})")
            return instance
        except Exception as e:
            logger.error(f"Failed to create warm container for {environment_type}: {e}")
            # Fallback to mock container if docker runtime fails
            mock_id = f"mock_fb_{environment_type}_{uuid.uuid4().hex[:8]}"
            return MockContainerInstance(mock_id, image_name, environment_type)

    async def _replenish_pool(self, env_type: str):
        """Top up the idle queue up to settings.POOL_MIN_IDLE."""
        q = self._idle_pools.get(env_type)
        if not q:
            return

        current_idle = q.qsize()
        needed = settings.POOL_MIN_IDLE - current_idle

        if needed > 0:
            tasks = [self._create_container(env_type) for _ in range(needed)]
            containers = await asyncio.gather(*tasks, return_exceptions=True)
            for c in containers:
                if isinstance(c, DockerContainerInstance):
                    await q.put(c)

        POOL_IDLE_CONTAINERS.labels(environment=env_type).set(q.qsize())

    async def _replenish_worker(self, env_type: str):
        """Continuous background monitor keeping the idle pool warmed."""
        while self._is_running:
            try:
                await self._replenish_pool(env_type)
            except Exception as e:
                logger.error(f"Error in replenish loop for {env_type}: {e}")
            await asyncio.sleep(settings.POOL_REPLENISH_INTERVAL_SECONDS)

    async def acquire(self, environment_type: str = "python-3.11", timeout_seconds: float = 5.0) -> DockerContainerInstance:
        """Acquires a pre-warmed sandbox from the queue in <120ms or creates on demand."""
        start_time = time.time()
        env_type = environment_type if environment_type in self._idle_pools else "python-3.11"
        q = self._idle_pools[env_type]

        container: Optional[DockerContainerInstance] = None

        try:
            # Fast path: instant pop from pre-warmed queue
            container = q.get_nowait()
        except asyncio.QueueEmpty:
            # Slow path: wait briefly or instantiate on demand
            try:
                container = await asyncio.wait_for(q.get(), timeout=1.0)
            except asyncio.TimeoutError:
                logger.warning(f"Warm pool depleted for {env_type}, spinning up on-demand container.")
                container = await self._create_container(env_type)

        async with self._lock:
            container.is_busy = True
            self._active_containers.add(container)

        POOL_IDLE_CONTAINERS.labels(environment=env_type).set(q.qsize())
        POOL_ACTIVE_CONTAINERS.labels(environment=env_type).set(len(self._active_containers))

        latency_ms = (time.time() - start_time) * 1000
        logger.info(
            f"Acquired sandbox {container.short_id} in {latency_ms:.2f}ms",
            extra={"container_id": container.short_id, "duration_ms": latency_ms}
        )

        return container

    async def release(self, container: DockerContainerInstance):
        """Asynchronously cleans up / terminates the used container and triggers background replenish."""
        async with self._lock:
            if container in self._active_containers:
                self._active_containers.remove(container)

        POOL_ACTIVE_CONTAINERS.labels(environment=container.environment_type).set(len(self._active_containers))

        # Asynchronously terminate the dirty container to preserve strict patch isolation
        asyncio.create_task(self._cleanup_container(container))

    async def _cleanup_container(self, container: DockerContainerInstance):
        try:
            await container.terminate(force=True)
        except Exception as e:
            logger.debug(f"Error terminating container {container.short_id}: {e}")

    async def shutdown(self):
        """Gracefully drains and destroys all containers in the pool."""
        self._is_running = False

        for task in self._replenish_tasks.values():
            task.cancel()

        # Drain and terminate idle containers
        for env_type, q in self._idle_pools.items():
            while not q.empty():
                try:
                    c = q.get_nowait()
                    await c.terminate(force=True)
                except Exception:
                    pass

        # Terminate active containers
        async with self._lock:
            for c in list(self._active_containers):
                await c.terminate(force=True)
            self._active_containers.clear()

        if self._docker_client:
            try:
                self._docker_client.close()
            except Exception:
                pass

        logger.info("WarmContainerPool has been completely drained and shut down.")

    def get_stats(self) -> Dict:
        """Returns live pool capacity statistics."""
        return {
            "idle_counts": {k: v.qsize() for k, v in self._idle_pools.items()},
            "active_count": len(self._active_containers),
            "mock_mode": self._mock_mode,
            "docker_connected": self.docker_connected,
        }


pool = WarmContainerPool()
