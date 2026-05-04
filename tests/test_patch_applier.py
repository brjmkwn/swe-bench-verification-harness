"""Unit tests for GitPatcher diff validation, sandboxed application, and rollback."""
import pytest
from app.core.patcher import GitPatcher, PatchValidationError
from app.sandbox.container import MockContainerInstance


VALID_DIFF = """diff --git a/app/calculator.py b/app/calculator.py
--- a/app/calculator.py
+++ b/app/calculator.py
@@ -1,3 +1,3 @@
 def add(a, b):
-    return a - b
+    return a + b
"""

INVALID_DIFF_NO_HEADERS = """
Just some random text that is not a diff.
def add(a, b):
    return a + b
"""

CONFLICTING_DIFF = """diff --git a/app/calculator.py b/app/calculator.py
--- a/app/calculator.py
+++ b/app/calculator.py
@@ -10,3 +10,3 @@
 INVALID_CONFLICT
"""


def test_valid_diff_syntax():
    """Verifies that a well-formed unified diff is validated successfully."""
    assert GitPatcher.validate_diff_syntax(VALID_DIFF) is True


def test_empty_diff_raises_error():
    """Verifies that an empty diff raises PatchValidationError."""
    with pytest.raises(PatchValidationError, match="Patch diff is empty"):
        GitPatcher.validate_diff_syntax("")

    with pytest.raises(PatchValidationError, match="Patch diff is empty"):
        GitPatcher.validate_diff_syntax("   \n\n  ")


def test_malformed_diff_raises_error():
    """Verifies that diff missing headers raises PatchValidationError."""
    with pytest.raises(PatchValidationError, match="missing unified diff headers"):
        GitPatcher.validate_diff_syntax(INVALID_DIFF_NO_HEADERS)


@pytest.mark.anyio
async def test_apply_patch_to_container_success():
    """Verifies applying a valid patch inside a sandbox container."""
    container = MockContainerInstance("test_c1", "swe-sandbox-python:3.11", "python-3.11")
    success, msg = await GitPatcher.apply_patch_to_container(container, VALID_DIFF)
    assert success is True
    assert "Patch applied successfully" in msg


@pytest.mark.anyio
async def test_apply_patch_conflict_rejection():
    """Verifies that conflicting diffs are cleanly rejected during dry-run."""
    container = MockContainerInstance("test_c2", "swe-sandbox-python:3.11", "python-3.11")
    success, msg = await GitPatcher.apply_patch_to_container(container, CONFLICTING_DIFF)
    assert success is False
    assert "Patch Application Conflict / Rejected" in msg


@pytest.mark.anyio
async def test_rollback_restores_clean_state():
    """Verifies that rollback command succeeds."""
    container = MockContainerInstance("test_c3", "swe-sandbox-python:3.11", "python-3.11")
    res = await GitPatcher.rollback(container)
    assert res is True
