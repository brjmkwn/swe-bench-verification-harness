"""Security jail and breakout tests ensuring micro-sandbox containment."""
import pytest
from app.sandbox.container import MockContainerInstance
from app.sandbox.security import SecurityPolicy


def test_security_policy_enforcement():
    host_config = SecurityPolicy.get_container_host_config()

    assert host_config["cap_drop"] == ["ALL"]
    assert host_config["network_mode"] == "none"
    assert host_config["read_only"] is True
    assert host_config["pids_limit"] == 100
    assert "no-new-privileges:true" in host_config["security_opt"]
    assert host_config["mem_limit"] == "1g"


@pytest.mark.anyio
async def test_adversarial_network_egress_blocked():
    """Verifies that untrusted code attempting network access is blocked."""
    container = MockContainerInstance("sec_c1", "swe-sandbox-python:3.11", "python-3.11")
    res = await container.exec_command("curl -s http://1.1.1.1 || nc -zv 8.8.8.8 53")
    assert res.exit_code != 0
    assert "Network is unreachable" in res.stderr or "network_mode=none" in res.stderr


@pytest.mark.anyio
async def test_adversarial_fork_bomb_blocked():
    """Verifies that fork bombs are stopped by PID limits."""
    container = MockContainerInstance("sec_c2", "swe-sandbox-python:3.11", "python-3.11")
    res = await container.exec_command("/bin/bash -c ':(){ :|:& };:'")
    assert res.exit_code != 0
    assert "Resource temporarily unavailable" in res.stderr or "pids-limit" in res.stderr


@pytest.mark.anyio
async def test_adversarial_rootfs_write_blocked():
    """Verifies that writing to root filesystem or /etc is blocked."""
    container = MockContainerInstance("sec_c3", "swe-sandbox-python:3.11", "python-3.11")
    res = await container.exec_command("touch /etc/hacked.txt")
    assert res.exit_code != 0
    assert "Read-only file system" in res.stderr
