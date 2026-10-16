#!/usr/bin/env python3
"""Create a controlled batch of Tracewell incidents and close them with resolution data.

Safe defaults:
  - targets localhost only unless --allow-remote is explicitly supplied
  - creates incidents through POST /incidents
  - closes a configurable proportion through POST /close with root cause + resolution
  - verifies incident detail and list visibility
  - uses bounded concurrency and prints a machine-readable JSON report

Example:
  python tests/load_test_incidents.py --base-url http://127.0.0.1:8000 --count 100 --concurrency 5
  python tests/load_test_incidents.py --base-url https://YOUR-STAGING-SERVICE --count 200 --concurrency 10 --allow-remote
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import random
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SERVICES = [
    "payment-api", "auth-service", "checkout-service", "orders-api",
    "notification-worker", "inventory-service", "search-api", "profile-service",
]
SCENARIOS = [
    {
        "category": "deployment",
        "title": "Elevated 5xx errors after deployment",
        "description": "Error rate increased shortly after a new application revision was deployed. Requests intermittently return HTTP 500.",
        "root_cause": "The new revision introduced an incompatible response mapping for an upstream dependency.",
        "resolution": "Rolled back the affected revision in the simulation, validated successful health checks, and confirmed the 5xx rate returned to baseline. Add a contract test before the next rollout.",
    },
    {
        "category": "database",
        "title": "Database connection pool exhausted",
        "description": "Request latency rose and requests timed out while the database connection pool remained at its configured limit.",
        "root_cause": "Connections were held longer than expected during a slow query path, exhausting the pool under peak load.",
        "resolution": "Cancelled the identified slow query in the simulation, reduced connection hold time, and verified pool utilization and request latency recovered. Add pool saturation alerts and query regression coverage.",
    },
    {
        "category": "dependency",
        "title": "Upstream dependency timeout",
        "description": "A downstream API experienced intermittent timeouts calling an upstream service. Retry volume increased during the incident.",
        "root_cause": "The upstream dependency exceeded the caller's timeout budget during a latency spike.",
        "resolution": "Applied a simulated dependency recovery, bounded retries with exponential backoff, and verified error rate and latency returned to the expected range. Add a dependency-level timeout dashboard.",
    },
    {
        "category": "authentication",
        "title": "Token validation failures",
        "description": "Valid users received HTTP 401 responses after a key rotation. Authentication failures increased across multiple clients.",
        "root_cause": "The token verifier was using a stale signing-key cache after key rotation.",
        "resolution": "Refreshed the simulated signing-key cache and verified valid tokens authenticate while invalid tokens remain rejected. Add key-rotation integration tests and a cache-refresh metric.",
    },
    {
        "category": "capacity",
        "title": "Worker queue backlog increasing",
        "description": "Background jobs accumulated faster than workers could process them, increasing end-to-end completion time.",
        "root_cause": "Worker concurrency was too low for the observed arrival rate, with a subset of jobs taking longer than normal.",
        "resolution": "Increased worker capacity in the simulation, drained the backlog, and verified queue age returned below the SLO threshold. Add queue-age alerts and load tests for expected peak traffic.",
    },
    {
        "category": "configuration",
        "title": "Misconfigured service endpoint",
        "description": "Requests failed after a configuration update changed the endpoint used by a service.",
        "root_cause": "The service endpoint configuration referenced an incorrect environment-specific hostname.",
        "resolution": "Restored the known-good endpoint configuration and verified successful connectivity from the affected service. Add configuration validation to deployment checks.",
    },
    {
        "category": "resource",
        "title": "Memory pressure and process restarts",
        "description": "Memory utilization rose steadily and the service restarted repeatedly under a burst of requests.",
        "root_cause": "A request-processing path retained large payloads longer than required, increasing memory pressure.",
        "resolution": "Disabled the problematic simulated processing path, confirmed memory usage stabilized, and verified restart frequency returned to zero. Add a memory regression test and heap monitoring.",
    },
    {
        "category": "observability",
        "title": "Health check reports false negative",
        "description": "The health endpoint marked the service unhealthy while the primary request path remained responsive.",
        "root_cause": "The health check depended on a non-critical optional component and conflated degraded status with total service failure.",
        "resolution": "Separated liveness from dependency readiness and verified the health response reflects the intended service contract. Add tests for optional dependency degradation.",
    },
]

def request_json(base_url: str, path: str, method: str = "GET",
                 payload: dict[str, Any] | None = None, timeout: float = 15.0) -> tuple[int, Any, float]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(
        base_url.rstrip("/") + path, data=body, headers=headers, method=method
    )
    start = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read()
            elapsed = time.perf_counter() - start
            parsed = json.loads(raw.decode("utf-8")) if raw else None
            return response.status, parsed, elapsed
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            parsed = json.loads(raw.decode("utf-8")) if raw else None
        except json.JSONDecodeError:
            parsed = raw.decode("utf-8", errors="replace")
        return exc.code, parsed, time.perf_counter() - start
    except Exception as exc:
        return 0, {"error": f"{type(exc).__name__}: {exc}"}, time.perf_counter() - start

def percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, int((len(ordered) - 1) * p))] * 1000, 2)

def run_one(index: int, args: argparse.Namespace) -> dict[str, Any]:
    rng = random.Random(args.seed + index)
    scenario = SCENARIOS[index % len(SCENARIOS)]
    service = SERVICES[rng.randrange(len(SERVICES))]
    now = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    suffix = f"{now}-{index:05d}-{rng.randrange(100000, 999999)}"
    incident_payload = {
        "title": f"[LOAD-{suffix}] {scenario['title']}",
        "service": service,
        "description": scenario["description"],
        "severity": ["SEV1", "SEV2", "SEV3", "SEV4"][index % 4],
        "category": scenario["category"],
    }
    result: dict[str, Any] = {
        "index": index, "service": service, "scenario": scenario["category"],
        "created": False, "resolved": False, "verified": False, "errors": [],
        "latencies_ms": {},
    }

    status, data, elapsed = request_json(args.base_url, "/incidents", "POST", incident_payload, args.timeout)
    result["latencies_ms"]["create"] = round(elapsed * 1000, 2)
    if status not in (200, 201) or not isinstance(data, dict) or not data.get("incident_id"):
        result["errors"].append({"stage": "create", "status": status, "response": data})
        return result
    incident_id = data["incident_id"]
    result.update({"incident_id": incident_id, "created": True, "create_status": status})

    if rng.random() < args.resolve_ratio:
        close_payload = {
            "incident_id": incident_id,
            "root_cause": scenario["root_cause"],
            "resolution": scenario["resolution"],
        }
        status, close_data, elapsed = request_json(args.base_url, "/close", "POST", close_payload, args.timeout)
        result["latencies_ms"]["close"] = round(elapsed * 1000, 2)
        if status not in (200, 201):
            result["errors"].append({"stage": "close", "status": status, "response": close_data})
        else:
            result["resolved"] = close_data.get("status") == "closed"
            result["close_status"] = status

    if args.verify:
        status, detail, elapsed = request_json(
            args.base_url, "/incidents/" + urllib.parse.quote(incident_id, safe=""), timeout=args.timeout
        )
        result["latencies_ms"]["detail"] = round(elapsed * 1000, 2)
        if status == 200 and isinstance(detail, dict):
            has_resolution = bool(detail.get("root_cause")) and bool(detail.get("resolution"))
            result["verified"] = (
                detail.get("incident_id", detail.get("id", incident_id)) == incident_id
                and (not result["resolved"] or (detail.get("status") == "closed" and has_resolution))
            )
            if result["resolved"] and not has_resolution:
                result["errors"].append({"stage": "verify_resolution", "status": status,
                                          "response": "Closed incident did not persist root_cause/resolution"})
        else:
            result["errors"].append({"stage": "verify_detail", "status": status, "response": detail})
    return result

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--count", type=int, default=100, help="number of incidents to create (default: 100)")
    parser.add_argument("--concurrency", type=int, default=5, help="parallel workers (default: 5)")
    parser.add_argument("--resolve-ratio", type=float, default=1.0, help="fraction to close with resolution data, 0..1")
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--seed", type=int, default=20261016)
    parser.add_argument("--verify", action="store_true", help="GET each incident and verify persisted resolution")
    parser.add_argument("--allow-remote", action="store_true", help="allow non-local targets; use staging, not production")
    parser.add_argument("--output", default="tracewell-load-report.json")
    args = parser.parse_args()

    if args.count < 1 or args.count > 10000:
        parser.error("--count must be between 1 and 10000")
    if args.concurrency < 1 or args.concurrency > 50:
        parser.error("--concurrency must be between 1 and 50")
    if not 0 <= args.resolve_ratio <= 1:
        parser.error("--resolve-ratio must be between 0 and 1")
    parsed = urllib.parse.urlparse(args.base_url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        parser.error("--base-url must be an absolute HTTP(S) URL")
    is_local = parsed.hostname in ("localhost", "127.0.0.1", "::1")
    if not is_local and not args.allow_remote:
        parser.error("Non-local target blocked. Use a staging environment and pass --allow-remote explicitly.")
    if not is_local and any(word in (parsed.hostname or "").lower() for word in ("prod", "production")):
        parser.error("Refusing a hostname that appears to be production. Use a dedicated staging service.")

    health_status, health, _ = request_json(args.base_url, "/health", timeout=args.timeout)
    if health_status != 200:
        print(f"Health check failed: HTTP {health_status}: {health}", file=sys.stderr)
        return 2
    print(f"Target: {args.base_url} | health={health} | count={args.count} | concurrency={args.concurrency}")
    started = time.perf_counter()
    results: list[dict[str, Any]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futures = [pool.submit(run_one, i, args) for i in range(args.count)]
        for future in concurrent.futures.as_completed(futures):
            row = future.result()
            results.append(row)
            if len(results) % max(1, args.count // 20) == 0 or len(results) == args.count:
                print(f"Progress: {len(results)}/{args.count}")

    elapsed = time.perf_counter() - started
    results.sort(key=lambda r: r["index"])

    # Verify created IDs are visible through the collection endpoint.
    list_status, list_data, list_elapsed = request_json(args.base_url, "/incidents", timeout=args.timeout)
    created_ids = {r.get("incident_id") for r in results if r.get("created")}
    if list_status == 200 and isinstance(list_data, list):
        visible_ids = {
            item.get("incident_id", item.get("id"))
            for item in list_data if isinstance(item, dict)
        }
        missing_ids = sorted(i for i in created_ids if i not in visible_ids)
    else:
        missing_ids = sorted(created_ids)
    all_latencies = [v for r in results for v in r["latencies_ms"].values()]
    summary = {
        "list_visibility_http_status": list_status,
        "list_visibility_latency_ms": round(list_elapsed * 1000, 2),
        "created_ids_missing_from_incident_list": missing_ids,
        "created_ids_visible_in_list": len(created_ids) - len(missing_ids),
        "base_url": args.base_url,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "count_requested": args.count,
        "count_created": sum(r["created"] for r in results),
        "count_resolved": sum(r["resolved"] for r in results),
        "count_verified": sum(r["verified"] for r in results),
        "count_failed": sum(bool(r["errors"]) or not r["created"] for r in results),
        "duration_seconds": round(elapsed, 3),
        "throughput_incidents_per_second": round(args.count / elapsed, 2) if elapsed else None,
        "latency_ms": {
            "p50": percentile(all_latencies, 0.50),
            "p95": percentile(all_latencies, 0.95),
            "p99": percentile(all_latencies, 0.99),
            "max": round(max(all_latencies), 2) if all_latencies else None,
        },
        "http_stage_errors": dict(Counter(
            e["stage"] for r in results for e in r["errors"]
        )),
        "incidents": results,
    }
    Path(args.output).write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k != "incidents"}, indent=2))
    print(f"Detailed report: {args.output}")
    return 1 if summary["count_failed"] else 0

if __name__ == "__main__":
    from pathlib import Path
    raise SystemExit(main())
