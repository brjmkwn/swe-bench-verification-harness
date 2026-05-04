"""Application configuration and settings."""
import os
import sys
from typing import Optional
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    # Service Configuration
    SERVICE_NAME: str = "swe-bench-verification-harness"
    ENVIRONMENT: str = "production"
    DEBUG: bool = False
    PORT: int = 8000
    HOST: str = "0.0.0.0"
    LOG_LEVEL: str = "INFO"

    # Sandbox Warm Pool Configuration
    POOL_MIN_IDLE: int = Field(default=3, description="Minimum idle pre-warmed sandboxes maintained in pool")
    POOL_MAX_IDLE: int = Field(default=10, description="Maximum idle containers in pool per runtime")
    POOL_REPLENISH_INTERVAL_SECONDS: float = 1.0
    CONTAINER_STARTUP_TIMEOUT_SECONDS: float = 10.0

    # Sandbox Security & Resource Limits
    SANDBOX_MEMORY_LIMIT: str = Field(default="1g", description="Hard memory constraint per container (e.g. 1g)")
    SANDBOX_CPU_QUOTA: float = Field(default=2.0, description="Max CPU cores allocated to sandbox")
    SANDBOX_PIDS_LIMIT: int = Field(default=100, description="PID limit to block fork bombs")
    SANDBOX_DEFAULT_TIMEOUT_SECONDS: int = Field(default=60, description="Default max execution timeout")
    SANDBOX_READ_ONLY_ROOTFS: bool = True
    SANDBOX_NETWORK_MODE: str = "none"  # Isolation mode: 'none' blocks external egress
    SANDBOX_USER: str = "1000:1000"     # Non-root user execution
    SANDBOX_WORKSPACE_DIR: str = "/workspace"
    SANDBOX_TMPFS_SIZE: str = "512m"

    # Container Images
    SANDBOX_IMAGE_PYTHON: str = "swe-sandbox-python:3.11"
    SANDBOX_IMAGE_NODE: str = "swe-sandbox-node:20"

    # Docker Daemon Connection
    DOCKER_BASE_URL: Optional[str] = Field(
        default=None,
        description="Docker daemon socket URI. Defaults to standard OS socket or npipe if None."
    )
    USE_MOCK_DOCKER: bool = Field(
        default=False,
        description="Fallback to lightweight mock sandbox runner when Docker daemon is unreachable."
    )

    # Observability & Metrics
    PROMETHEUS_METRICS_ENABLED: bool = True
    OPIK_API_KEY: Optional[str] = None
    OPIK_PROJECT_NAME: str = "swe-bench-evals"
    OPIK_WORKSPACE: Optional[str] = None


settings = Settings()
