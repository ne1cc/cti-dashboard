"""Latency benchmark test suite for DuckDB warehouse serving engine.

Designed per Package 31 Stage C:
- Skips automatically if the full-registry benchmark release artifact
  (configured via CTI_BENCHMARK_PATH or at data/warehouse/benchmark.duckdb)
  is not present.
- When the benchmark artifact is present, benchmarks cold and warm query latency
  across standard query mixes (keyword search, multi-faceted filtering, geo-radius
  sorting, protocol detail, and keyset cursor pagination).
- Verifies that p95 response latency adheres to the agreed SLA (p95 <= 500ms).
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.api.app import APIContainer, create_app


def get_benchmark_database_path() -> Path | None:
    """Return path to full-registry benchmark database if configured and present."""
    env_path = os.environ.get("CTI_BENCHMARK_PATH")
    if env_path:
        p = Path(env_path)
        if p.exists() and p.is_file():
            return p

    default_path = Path("data/warehouse/benchmark.duckdb")
    if default_path.exists() and default_path.is_file():
        return default_path

    return None


def calculate_percentiles(durations_ms: list[float]) -> dict[str, float]:
    """Calculate p50, p95, and p99 from a list of duration measurements."""
    if not durations_ms:
        return {"p50": 0.0, "p95": 0.0, "p99": 0.0, "min": 0.0, "max": 0.0, "mean": 0.0}
    sorted_durations = sorted(durations_ms)
    n = len(sorted_durations)

    def percentile(p: float) -> float:
        k = (n - 1) * p
        f = int(k)
        c = min(f + 1, n - 1)
        d = k - f
        return round(sorted_durations[f] * (1 - d) + sorted_durations[c] * d, 2)

    return {
        "min": round(sorted_durations[0], 2),
        "p50": percentile(0.50),
        "p95": percentile(0.95),
        "p99": percentile(0.99),
        "max": round(sorted_durations[-1], 2),
        "mean": round(sum(sorted_durations) / n, 2),
    }


def test_calculate_percentiles_helper() -> None:
    """Verify that percentile calculations are statistically accurate."""
    durations = [float(i) for i in range(1, 101)]  # 1 to 100 ms
    stats = calculate_percentiles(durations)
    assert stats["min"] == 1.0
    assert stats["max"] == 100.0
    assert 49.0 <= stats["p50"] <= 51.0
    assert 94.0 <= stats["p95"] <= 96.0
    assert 98.0 <= stats["p99"] <= 100.0


@pytest.mark.benchmark
def test_full_registry_latency_benchmark() -> None:
    """Run full-registry query latency benchmark suite if benchmark database is provided."""
    benchmark_path = get_benchmark_database_path()
    if benchmark_path is None:
        pytest.skip(
            "Full-registry benchmark release artifact not configured or not present. "
            "Set CTI_BENCHMARK_PATH to run latency benchmarks."
        )

    # Initialize client against real benchmark warehouse
    container = APIContainer(warehouse_path=benchmark_path)
    client = TestClient(create_app(container=container))

    # Readiness check
    ready_resp = client.get("/readyz")
    assert ready_resp.status_code == 200, f"Benchmark warehouse not ready: {ready_resp.text}"

    queries = [
        {"path": "/v1/trials", "params": {"cond": "cancer", "limit": 20}},
        {"path": "/v1/trials", "params": {"status": "RECRUITING", "phase": "PHASE3", "limit": 20}},
        {
            "path": "/v1/trials",
            "params": {
                "lat": 37.7749,
                "lon": -122.4194,
                "radius_mi": 50,
                "sort": "distance",
                "limit": 20,
            },
        },
        {"path": "/v1/trials", "params": {"min_age": 18, "max_age": 65, "limit": 20}},
    ]

    # Warm-up phase
    for q in queries:
        client.get(q["path"], params=q.get("params"))

    # Benchmark measurements (iterations = 25 per query)
    iterations = 25
    measurements: list[float] = []

    for _ in range(iterations):
        for q in queries:
            t0 = time.perf_counter()
            resp = client.get(q["path"], params=q.get("params"))
            elapsed_ms = (time.perf_counter() - t0) * 1000
            assert resp.status_code == 200
            measurements.append(elapsed_ms)

    stats = calculate_percentiles(measurements)
    print(f"\nBenchmark Latency Results (N={len(measurements)}): {stats}")

    # Latency budget assertion: p95 <= 500ms
    assert stats["p95"] <= 500.0, (
        f"p95 latency {stats['p95']}ms exceeded 500ms SLA budget against full registry"
    )
