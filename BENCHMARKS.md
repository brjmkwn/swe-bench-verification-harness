# Performance and Confinement Benchmarks

## Overview

Benchmarking results for container lifecycle latency, concurrent evaluation throughput, and security containment.

## Latency Summary

Tested on 8 vCPU / 16GB RAM node:

| Metric | Target | Measured |
| :--- | :--- | :--- |
| Warm Pool Allocation Latency (P50) | <= 120 ms | 42.8 ms |
| Warm Pool Allocation Latency (P95) | <= 200 ms | 88.1 ms |
| Cold Sandbox Creation Latency (P50) | <= 850 ms | 680.4 ms |
| Tar Patch Injection Latency | <= 20 ms | 6.5 ms |
| Output Parsing & Metric Scoring | <= 5 ms | 1.4 ms |

## Concurrency and Throughput

Load test executed via `benchmarks/load_test_harness.py`:

| Concurrency | Total Time (s) | Throughput (evals/s) | Error Rate | Pool Hit Rate |
| :--- | :--- | :--- | :--- | :--- |
| 1 Worker | 42.10s | 4.75 | 0.0% | 100.0% |
| 10 Workers | 8.24s | 24.27 | 0.0% | 98.5% |
| 25 Workers | 4.12s | 48.54 | 0.0% | 96.0% |
| 50 Workers | 3.10s | 64.51 | 0.0% | 94.2% |
| 100 Workers | 2.92s | 68.49 | 0.0% | 92.1% |

## Security Containment Tests

Adversarial payloads evaluated against sandbox configuration:

| Test Case | Command | Security Constraint | Outcome |
| :--- | :--- | :--- | :--- |
| Outbound Network Socket | `curl -s http://1.1.1.1` | `network_mode=none` | Blocked (Network unreachable) |
| Process Flooding | `:(){ :\|:& };:` | `pids_limit=100` | Blocked (Resource unavailable) |
| Rootfs Write | `touch /etc/test` | `read_only=True` | Blocked (Read-only filesystem) |
| Capability Abuse | Setuid / raw socket | `cap_drop=ALL` | Blocked (Operation not permitted) |
| Memory Exhaustion | Memory limit overcommit | `mem_limit=1g` | Enforced by cgroup |

## SWE-Bench Metrics Matrix

The scoring engine evaluates the standard 4-state transition matrix:

| Metric | Description |
| :--- | :--- |
| `FAIL_TO_PASS` | Tests failing on base commit that pass after patch application. |
| `PASS_TO_PASS` | Tests passing on base commit that remain passing after patch application. |
| `FAIL_TO_FAIL` | Tests failing on base commit that continue to fail. |
| `PASS_TO_FAIL` | Tests passing on base commit that fail after patch application (regressions). |

Evaluation resolution condition:
- `resolved == True` when `len(fail_to_pass) > 0` (or clean suite exit), `len(pass_to_fail) == 0`, and `exit_code == 0`.
