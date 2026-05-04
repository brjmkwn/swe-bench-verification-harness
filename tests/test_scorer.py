"""Unit tests for SWEBenchScorer test output parsing and transition metric computations."""
from app.core.scorer import SWEBenchScorer
from app.schemas import TestStatus


BASE_PYTEST_OUTPUT = """
============================= test session starts ==============================
rootdir: /workspace
collected 4 items

tests/test_math.py::test_addition PASSED                                  [ 25%]
tests/test_math.py::test_subtraction PASSED                               [ 50%]
tests/test_math.py::test_division_by_zero FAILED                          [ 75%]
tests/test_math.py::test_power FAILED                                     [100%]

=================================== FAILURES ===================================
___________________________ test_division_by_zero ______________________________
ZeroDivisionError: division by zero
=========================== 2 failed, 2 passed in 0.12s ===========================
"""

PATCHED_RESOLVED_PYTEST_OUTPUT = """
============================= test session starts ==============================
rootdir: /workspace
collected 4 items

tests/test_math.py::test_addition PASSED                                  [ 25%]
tests/test_math.py::test_subtraction PASSED                               [ 50%]
tests/test_math.py::test_division_by_zero PASSED                          [ 75%]
tests/test_math.py::test_power PASSED                                     [100%]

============================== 4 passed in 0.10s ===============================
"""

PATCHED_REGRESSION_PYTEST_OUTPUT = """
============================= test session starts ==============================
rootdir: /workspace
collected 4 items

tests/test_math.py::test_addition PASSED                                  [ 25%]
tests/test_math.py::test_subtraction FAILED                               [ 50%]
tests/test_math.py::test_division_by_zero PASSED                          [ 75%]
tests/test_math.py::test_power FAILED                                     [100%]

=========================== 2 failed, 2 passed in 0.15s ===========================
"""

JEST_BASE_OUTPUT = """
 PASS  tests/auth.test.js
 FAIL  tests/api.test.js
"""

JEST_PATCHED_OUTPUT = """
 PASS  tests/auth.test.js
 PASS  tests/api.test.js
"""

CARGO_BASE_OUTPUT = """
running 2 tests
test parser::test_valid ... ok
test parser::test_invalid_syntax ... FAILED
"""

CARGO_PATCHED_OUTPUT = """
running 2 tests
test parser::test_valid ... ok
test parser::test_invalid_syntax ... ok
"""


def test_pytest_output_parsing():
    """Verifies that individual test statuses are accurately parsed from pytest output."""
    parsed = SWEBenchScorer.parse_test_output(BASE_PYTEST_OUTPUT)
    assert parsed["tests/test_math.py::test_addition"] == TestStatus.PASSED
    assert parsed["tests/test_math.py::test_subtraction"] == TestStatus.PASSED
    assert parsed["tests/test_math.py::test_division_by_zero"] == TestStatus.FAILED
    assert parsed["tests/test_math.py::test_power"] == TestStatus.FAILED


def test_resolved_fix_metric_calculation():
    """Verifies that fixing all failing tests with no regressions yields resolved=True."""
    metrics = SWEBenchScorer.compute_metrics(
        base_output=BASE_PYTEST_OUTPUT,
        patched_output=PATCHED_RESOLVED_PYTEST_OUTPUT,
        base_exit_code=1,
        patched_exit_code=0
    )

    assert set(metrics.fail_to_pass) == {
        "tests/test_math.py::test_division_by_zero",
        "tests/test_math.py::test_power",
    }
    assert set(metrics.pass_to_pass) == {
        "tests/test_math.py::test_addition",
        "tests/test_math.py::test_subtraction",
    }
    assert metrics.pass_to_fail == []
    assert metrics.fail_to_fail == []
    assert metrics.resolved is True
    assert metrics.pass_rate == 100.0
    assert metrics.resolution_status == "FULLY_RESOLVED"


def test_regression_detection():
    """Verifies that breaking a previously passing test is caught in pass_to_fail and marks resolved=False."""
    metrics = SWEBenchScorer.compute_metrics(
        base_output=BASE_PYTEST_OUTPUT,
        patched_output=PATCHED_REGRESSION_PYTEST_OUTPUT,
        base_exit_code=1,
        patched_exit_code=1
    )

    assert "tests/test_math.py::test_subtraction" in metrics.pass_to_fail
    assert "tests/test_math.py::test_division_by_zero" in metrics.fail_to_pass
    assert "tests/test_math.py::test_power" in metrics.fail_to_fail
    assert metrics.resolved is False
    assert metrics.resolution_status == "REGRESSION_INTRODUCED"


def test_jest_metrics_calculation():
    """Verifies SWE-bench scoring on Jest test suites."""
    metrics = SWEBenchScorer.compute_metrics(
        base_output=JEST_BASE_OUTPUT,
        patched_output=JEST_PATCHED_OUTPUT,
        base_exit_code=1,
        patched_exit_code=0
    )

    assert "tests/api.test.js" in metrics.fail_to_pass
    assert "tests/auth.test.js" in metrics.pass_to_pass
    assert metrics.resolved is True


def test_cargo_metrics_calculation():
    """Verifies SWE-bench scoring on Rust Cargo test suites."""
    metrics = SWEBenchScorer.compute_metrics(
        base_output=CARGO_BASE_OUTPUT,
        patched_output=CARGO_PATCHED_OUTPUT,
        base_exit_code=1,
        patched_exit_code=0
    )

    assert "parser::test_invalid_syntax" in metrics.fail_to_pass
    assert "parser::test_valid" in metrics.pass_to_pass
    assert metrics.resolved is True
