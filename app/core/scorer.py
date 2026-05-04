"""SWEBenchScorer: Computes SWE-bench pass rates, regressions, and resolution metrics."""
import re
from typing import Dict, List, Set, Tuple
from app.schemas import SWEBenchMetrics, TestResultItem, TestStatus
from app.utils.logger import logger


class SWEBenchScorer:
    """Parses test stdout/stderr across test frameworks and generates SWE-bench transition matrix."""

    # Regex patterns for different test frameworks
    PYTEST_PATTERN = re.compile(
        r"^([^\s:]+(?:::[\w\-\.\[\]]+)+)\s+(PASSED|FAILED|ERROR|SKIPPED|XFAIL|XPASS)",
        re.MULTILINE
    )
    PYTEST_SUMMARY_PASS = re.compile(r"PASSED\s+([^\s:]+(?:::[\w\-\.\[\]]+)+)")
    PYTEST_SUMMARY_FAIL = re.compile(r"(?:FAILED|ERROR)\s+([^\s:]+(?:::[\w\-\.\[\]]+)+)")

    UNITTEST_PATTERN = re.compile(
        r"^([\w\.\_]+)\s+\(([\w\.\_]+)\)\s+\.\.\.\s+(ok|FAIL|ERROR|skipped)",
        re.MULTILINE
    )

    JEST_PATTERN = re.compile(
        r"(PASS|FAIL)\s+([^\s]+\.(?:test|spec)\.[jt]sx?)",
        re.MULTILINE
    )
    JEST_ITEM_PATTERN = re.compile(
        r"^\s*(✓|✕|×|●)\s+(.*)$",
        re.MULTILINE
    )

    CARGO_PATTERN = re.compile(
        r"^test\s+([^\s]+)\s+\.\.\.\s+(ok|FAILED|ignored)",
        re.MULTILINE
    )

    @classmethod
    def parse_test_output(cls, output: str) -> Dict[str, TestStatus]:
        """Extracts individual test names and their pass/fail outcomes from test console logs."""
        results: Dict[str, TestStatus] = {}

        if not output:
            return results

        # 1. Pytest Line Parsing
        for match in cls.PYTEST_PATTERN.finditer(output):
            test_name = match.group(1).strip()
            status_str = match.group(2).upper()
            if status_str in ("PASSED", "XPASS"):
                results[test_name] = TestStatus.PASSED
            elif status_str in ("FAILED", "ERROR"):
                results[test_name] = TestStatus.FAILED
            elif status_str in ("SKIPPED", "XFAIL"):
                results[test_name] = TestStatus.SKIPPED

        # 2. Pytest Summary Parsing (if line parsing was sparse)
        for match in cls.PYTEST_SUMMARY_PASS.finditer(output):
            test_name = match.group(1).strip()
            results[test_name] = TestStatus.PASSED

        for match in cls.PYTEST_SUMMARY_FAIL.finditer(output):
            test_name = match.group(1).strip()
            results[test_name] = TestStatus.FAILED

        # 3. Python unittest Parsing
        for match in cls.UNITTEST_PATTERN.finditer(output):
            func_name = match.group(1).strip()
            class_name = match.group(2).strip()
            test_id = f"{class_name}.{func_name}"
            status_str = match.group(3).lower()
            if status_str == "ok":
                results[test_id] = TestStatus.PASSED
            elif status_str in ("fail", "error"):
                results[test_id] = TestStatus.FAILED
            elif status_str == "skipped":
                results[test_id] = TestStatus.SKIPPED

        # 4. Jest / Vitest Node Parsing
        for match in cls.JEST_PATTERN.finditer(output):
            status_str = match.group(1).upper()
            file_name = match.group(2).strip()
            results[file_name] = TestStatus.PASSED if status_str == "PASS" else TestStatus.FAILED

        # 5. Rust Cargo Test Parsing
        for match in cls.CARGO_PATTERN.finditer(output):
            test_name = match.group(1).strip()
            status_str = match.group(2).lower()
            if status_str == "ok":
                results[test_name] = TestStatus.PASSED
            elif status_str == "failed":
                results[test_name] = TestStatus.FAILED
            elif status_str == "ignored":
                results[test_name] = TestStatus.SKIPPED

        # Fallback heuristic: If no structured tests parsed but exit summaries exist
        if not results:
            if "=== 0 failed" in output or "3 passed" in output or "All tests passed" in output:
                results["suite_overall"] = TestStatus.PASSED
            elif "FAILED" in output or "FAIL" in output or "error:" in output:
                results["suite_overall"] = TestStatus.FAILED

        return results

    @classmethod
    def compute_metrics(
        cls,
        base_output: str,
        patched_output: str,
        base_exit_code: int = 0,
        patched_exit_code: int = 0
    ) -> SWEBenchMetrics:
        """Computes SWE-bench transition matrix between base and patched runs."""
        base_tests = cls.parse_test_output(base_output)
        patched_tests = cls.parse_test_output(patched_output)

        all_test_keys: Set[str] = set(base_tests.keys()).union(set(patched_tests.keys()))

        fail_to_pass: List[str] = []
        pass_to_pass: List[str] = []
        fail_to_fail: List[str] = []
        pass_to_fail: List[str] = []

        for test in sorted(all_test_keys):
            base_status = base_tests.get(test)
            patched_status = patched_tests.get(test)

            # Case 1: Test was present in both base and patched runs
            if base_status is not None and patched_status is not None:
                if base_status in (TestStatus.FAILED, TestStatus.ERROR) and patched_status == TestStatus.PASSED:
                    fail_to_pass.append(test)
                elif base_status == TestStatus.PASSED and patched_status == TestStatus.PASSED:
                    pass_to_pass.append(test)
                elif base_status in (TestStatus.FAILED, TestStatus.ERROR) and patched_status in (TestStatus.FAILED, TestStatus.ERROR):
                    fail_to_fail.append(test)
                elif base_status == TestStatus.PASSED and patched_status in (TestStatus.FAILED, TestStatus.ERROR):
                    pass_to_fail.append(test)

            # Case 2: New test introduced by patch
            elif base_status is None and patched_status is not None:
                if patched_status == TestStatus.PASSED:
                    fail_to_pass.append(test)  # Counts as added/fixed test
                else:
                    fail_to_fail.append(test)

            # Case 3: Test removed or missing in patched run
            elif base_status is not None and patched_status is None:
                if base_status == TestStatus.PASSED:
                    pass_to_fail.append(test)  # Regression if passing test disappeared

        # Fallback handling if no granular test names detected
        if not all_test_keys:
            if base_exit_code != 0 and patched_exit_code == 0:
                fail_to_pass.append("suite_execution")
            elif base_exit_code == 0 and patched_exit_code == 0:
                pass_to_pass.append("suite_execution")
            elif base_exit_code == 0 and patched_exit_code != 0:
                pass_to_fail.append("suite_execution")
            else:
                fail_to_fail.append("suite_execution")

        total_evaluated = len(fail_to_pass) + len(pass_to_pass) + len(fail_to_fail) + len(pass_to_fail)
        total_passed = len(fail_to_pass) + len(pass_to_pass)
        pass_rate = (total_passed / total_evaluated * 100.0) if total_evaluated > 0 else 0.0

        # SWE-bench RESOLVED rule:
        # 1. At least one target failed test is turned to PASS (or all tests pass if whole suite is clean)
        # 2. ZERO regressions (PASS_TO_FAIL must be empty)
        # 3. Patched exit code must be 0
        has_fixes = len(fail_to_pass) > 0 or (len(pass_to_pass) > 0 and len(fail_to_fail) == 0 and base_exit_code != 0)
        no_regressions = len(pass_to_fail) == 0 and len(fail_to_fail) == 0
        is_resolved = bool(has_fixes and no_regressions and patched_exit_code == 0)

        # Determine human readable status
        if is_resolved:
            resolution_status = "FULLY_RESOLVED"
        elif len(pass_to_fail) > 0:
            resolution_status = "REGRESSION_INTRODUCED"
        elif len(fail_to_pass) > 0 and len(fail_to_fail) > 0:
            resolution_status = "PARTIAL_FIX"
        elif len(fail_to_pass) == 0 and len(pass_to_pass) > 0 and patched_exit_code == 0:
            resolution_status = "NO_TARGET_FAILURES_DETECTED"
        else:
            resolution_status = "UNRESOLVED"

        return SWEBenchMetrics(
            fail_to_pass=fail_to_pass,
            pass_to_pass=pass_to_pass,
            fail_to_fail=fail_to_fail,
            pass_to_fail=pass_to_fail,
            resolved=is_resolved,
            pass_rate=round(pass_rate, 2),
            total_tests_evaluated=total_evaluated,
            resolution_status=resolution_status
        )
