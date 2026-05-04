"""Security guardrails, capability drops, and cgroup isolation policies."""
from typing import Any, Dict, List
from app.config import settings


class SecurityPolicy:
    """Encapsulates host-level and runtime security configurations for sandboxes."""

    @staticmethod
    def get_container_host_config() -> Dict[str, Any]:
        """Generates standard Docker HostConfig dictionary with strict sandboxing."""
        # Convert CPU quota to nanoseconds for nano_cpus (e.g. 2.0 CPUs = 2_000_000_000)
        nano_cpus = int(settings.SANDBOX_CPU_QUOTA * 1_000_000_000)

        host_config = {
            "cap_drop": ["ALL"],
            "security_opt": ["no-new-privileges:true"],
            "network_mode": settings.SANDBOX_NETWORK_MODE,
            "read_only": settings.SANDBOX_READ_ONLY_ROOTFS,
            "mem_limit": settings.SANDBOX_MEMORY_LIMIT,
            "memswap_limit": settings.SANDBOX_MEMORY_LIMIT,
            "nano_cpus": nano_cpus,
            "pids_limit": settings.SANDBOX_PIDS_LIMIT,
            "tmpfs": {
                "/tmp": "rw,nosuid,nodev,size=256m",
                settings.SANDBOX_WORKSPACE_DIR: f"rw,exec,nosuid,nodev,size={settings.SANDBOX_TMPFS_SIZE}",
            },
            "ulimits": [
                {"name": "core", "soft": 0, "hard": 0},
                {"name": "nofile", "soft": 1024, "hard": 2048},
            ],
            "restart_policy": {"Name": "no"},
        }
        return host_config

    @staticmethod
    def get_container_create_kwargs(image: str, environment_vars: Dict[str, str] = None) -> Dict[str, Any]:
        """Builds parameters for docker container creation."""
        env = {
            "PYTHONUNBUFFERED": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "HOME": settings.SANDBOX_WORKSPACE_DIR,
            "WORKSPACE": settings.SANDBOX_WORKSPACE_DIR,
            "CI": "true",
            "DEBIAN_FRONTEND": "noninteractive",
        }
        if environment_vars:
            env.update(environment_vars)

        return {
            "image": image,
            "command": ["sleep", "infinity"],  # Keeps container alive in idle warm pool
            "working_dir": settings.SANDBOX_WORKSPACE_DIR,
            "user": settings.SANDBOX_USER,
            "environment": env,
            "stdin_open": False,
            "tty": False,
            "detach": True,
        }
