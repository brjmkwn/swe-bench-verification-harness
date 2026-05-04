"""Git patch validation, application, and rollback utilities."""
import re
from typing import Tuple
from app.sandbox.container import DockerContainerInstance
from app.utils.logger import logger


class PatchValidationError(Exception):
    """Raised when diff format is malformed or invalid."""
    pass


class PatchApplyError(Exception):
    """Raised when git apply fails with conflicts or syntax errors."""
    pass


class GitPatcher:
    """Validates diff syntax and applies patches inside sandbox containers."""

    UNIFIED_DIFF_HEADER_REGEX = re.compile(r"^diff --git a/.* b/.*|^--- [ab]/.*", re.MULTILINE)
    HUNK_HEADER_REGEX = re.compile(r"^@@ -\d+(,\d+)? \+\d+(,\d+)? @@", re.MULTILINE)

    @classmethod
    def validate_diff_syntax(cls, patch_diff: str) -> bool:
        """Validates that patch diff contains unified diff headers."""
        if not patch_diff or not patch_diff.strip():
            raise PatchValidationError("Patch diff is empty.")

        has_file_header = bool(cls.UNIFIED_DIFF_HEADER_REGEX.search(patch_diff))
        has_hunk_header = bool(cls.HUNK_HEADER_REGEX.search(patch_diff))

        if not (has_file_header or has_hunk_header):
            raise PatchValidationError(
                "Invalid patch format: missing unified diff headers ('diff --git', '--- a/...', or '@@ -... @@')"
            )
        return True

    @classmethod
    async def apply_patch_to_container(
        cls,
        container: DockerContainerInstance,
        patch_diff: str,
        workspace_dir: str = "/workspace"
    ) -> Tuple[bool, str]:
        """Writes patch into container, runs dry-run check, and applies changes."""
        try:
            cls.validate_diff_syntax(patch_diff)
        except PatchValidationError as e:
            return False, f"Diff Validation Error: {str(e)}"

        patch_file_path = f"{workspace_dir}/patch.diff"
        write_ok = await container.write_file(patch_file_path, patch_diff)
        if not write_ok:
            return False, "Failed to write patch diff to container workspace."

        # Dry-run with git apply --check
        check_cmd = f"git apply --check {patch_file_path} || patch -p1 --dry-run < {patch_file_path}"
        check_res = await container.exec_command(check_cmd, timeout_seconds=15.0, workdir=workspace_dir)

        if check_res.exit_code != 0:
            error_msg = check_res.stderr or check_res.stdout or "git apply --check rejected patch"
            logger.warning(f"Patch rejected in container {container.short_id}: {error_msg}")
            return False, f"Patch Application Conflict / Rejected: {error_msg.strip()}"

        # Apply patch
        apply_cmd = f"git apply --whitespace=fix {patch_file_path} || patch -p1 < {patch_file_path}"
        apply_res = await container.exec_command(apply_cmd, timeout_seconds=15.0, workdir=workspace_dir)

        if apply_res.exit_code != 0:
            error_msg = apply_res.stderr or apply_res.stdout or "git apply failed during application"
            return False, f"Patch Application Error: {error_msg.strip()}"

        logger.info(f"Applied patch in container {container.short_id}")
        return True, "Patch applied successfully."

    @classmethod
    async def rollback(cls, container: DockerContainerInstance, workspace_dir: str = "/workspace") -> bool:
        """Rolls back any changes in the repository to a clean state."""
        rollback_cmd = "git checkout -f && git clean -fdx"
        res = await container.exec_command(rollback_cmd, timeout_seconds=15.0, workdir=workspace_dir)
        return res.exit_code == 0
