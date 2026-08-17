#!/usr/bin/env python3
"""Dependency-free 250-concurrent-client HTTP API qualification."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import math
import time
import urllib.error
import urllib.request


def percentile(values: list[float], value: float) -> float:
    ordered = sorted(values)
    index = min(math.ceil(value * len(ordered)) - 1, len(ordered) - 1)
    return ordered[index]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument("--concurrency", type=int, default=250)
    parser.add_argument("--requests", type=int, default=1000)
    args = parser.parse_args()

    endpoint = f"{args.url.rstrip('/')}/api/scenarios"

    def invoke(_: int) -> tuple[int, float]:
        request = urllib.request.Request(endpoint, headers={"x-demo-token": args.token})
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                response.read()
                return response.status, time.perf_counter() - started
        except (urllib.error.URLError, TimeoutError):
            return 0, time.perf_counter() - started

    started = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as executor:
        results = list(executor.map(invoke, range(args.requests)))
    elapsed = time.perf_counter() - started
    latencies = [latency for status, latency in results if status == 200]
    failures = len(results) - len(latencies)
    report = {
        "concurrency": args.concurrency,
        "requests": args.requests,
        "successes": len(latencies),
        "failures": failures,
        "elapsed_seconds": round(elapsed, 3),
        "requests_per_second": round(len(results) / elapsed, 3),
        "p50_ms": round(percentile(latencies, 0.50) * 1000, 3) if latencies else None,
        "p95_ms": round(percentile(latencies, 0.95) * 1000, 3) if latencies else None,
        "p99_ms": round(percentile(latencies, 0.99) * 1000, 3) if latencies else None,
    }
    print(json.dumps(report, indent=2))
    raise SystemExit(1 if failures else 0)


if __name__ == "__main__":
    main()
