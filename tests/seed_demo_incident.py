#!/usr/bin/env python3
"""
Seed one synthetic, evidence-rich Tracewell incident through the public API.

Usage:
  python seed_demo_incident.py --api https://tracewell-api-475552873570.us-central1.run.app

This script uses Tracewell's own API endpoints (not direct Firestore writes).
It creates a NEW incident every run. Run once for the demo unless you want duplicates.
"""
import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def request_json(base_url, path, method="GET", payload=None):
    url = base_url.rstrip("/") + path
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    try:
        with urlopen(req, timeout=60) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {path} returned HTTP {exc.code}: {body}") from exc
    except URLError as exc:
        raise RuntimeError(f"Could not reach {url}: {exc}") from exc


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default="https://tracewell-api-475552873570.us-central1.run.app")
    args = parser.parse_args()
    base = args.api.rstrip("/")

    health = request_json(base, "/health")
    print("API health:", health)
    if health.get("status") != "ok":
        raise RuntimeError("API health check did not return status=ok")

    now = datetime.now(timezone.utc).replace(microsecond=0)
    stamp = lambda minutes_ago: (now - timedelta(minutes=minutes_ago)).isoformat().replace("+00:00", "Z")

    incident_payload = {
        "title": "[DEMO] Payment API 500s after malformed DATABASE_URL deployment",
        "service": "payment-api",
        "severity": "SEV1",
        "category": "deployment_configuration",
        "description": (
            "SYNTHETIC DEMO INCIDENT. payment-api began returning HTTP 500 responses "
            "immediately after revision payment-api-00042 received 100% of traffic. "
            "Logs identify DATABASE_URL_INVALID on that revision. The payment database "
            "and cache health checks remain healthy. Determine the most evidence-supported "
            "cause and separate direct evidence from inference."
        ),
    }
    created = request_json(base, "/incidents", "POST", incident_payload)
    incident_id = created.get("incident_id") or created.get("id")
    if not incident_id:
        raise RuntimeError(f"Incident creation response had no incident_id: {created}")
    print(f"\nCreated incident: {incident_id}")

    events = [
        {
            "event_type": "deployment",
            "service": "payment-api",
            "description": "Revision payment-api-00042 deployed; traffic shifted from payment-api-00041 to 100% on payment-api-00042. DATABASE_URL environment variable changed in this deployment.",
            "timestamp": stamp(18),
            "evidence": {
                "source": "Cloud Deploy (synthetic demo fixture)",
                "severity": "INFO",
                "revision": "payment-api-00042",
                "old_revision": "payment-api-00041",
                "new_revision": "payment-api-00042",
                "traffic_percent": 100,
                "change": "DATABASE_URL environment variable changed",
                "verification_status": "verified",
            },
        },
        {
            "event_type": "error_log",
            "service": "payment-api",
            "description": "Startup error on revision payment-api-00042: invalid database URL scheme; expected postgresql:// or postgres://. Error code DATABASE_URL_INVALID.",
            "timestamp": stamp(17),
            "evidence": {
                "source": "Cloud Logging (synthetic demo fixture)",
                "severity": "ERROR",
                "revision": "payment-api-00042",
                "error_code": "DATABASE_URL_INVALID",
                "sample": "ValueError: invalid database URL scheme; expected postgresql:// or postgres://",
                "verification_status": "verified",
            },
        },
        {
            "event_type": "error_log",
            "service": "payment-api",
            "description": "POST /pay returned HTTP 500 because database client initialization failed with DATABASE_URL_INVALID; 214 errors in 60 seconds on revision payment-api-00042.",
            "timestamp": stamp(16),
            "evidence": {
                "source": "Cloud Logging (synthetic demo fixture)",
                "severity": "ERROR",
                "revision": "payment-api-00042",
                "error_code": "DATABASE_URL_INVALID",
                "http_status": 500,
                "route": "POST /pay",
                "count": 214,
                "window_seconds": 60,
                "verification_status": "verified",
            },
        },
        {
            "event_type": "health_check",
            "service": "payment-db",
            "description": "payment-db health check passed; connection acceptance and SELECT 1 query probe are healthy. No database-side outage detected.",
            "timestamp": stamp(15),
            "evidence": {
                "source": "Cloud Monitoring (synthetic demo fixture)",
                "severity": "INFO",
                "health": "healthy",
                "probe": "SELECT 1",
                "connection_acceptance": "normal",
                "verification_status": "verified",
            },
        },
        {
            "event_type": "health_check",
            "service": "payment-cache",
            "description": "payment-cache health check passed; hit rate and latency remain at baseline.",
            "timestamp": stamp(14),
            "evidence": {
                "source": "Cloud Monitoring (synthetic demo fixture)",
                "severity": "INFO",
                "health": "healthy",
                "hit_rate_percent": 91,
                "p95_latency_ms": 4,
                "verification_status": "verified",
            },
        },
        {
            "event_type": "traffic_allocation",
            "service": "payment-api",
            "description": "100% of requests route to payment-api-00042. Previous revision payment-api-00041 was healthy before the traffic shift.",
            "timestamp": stamp(18),
            "evidence": {
                "source": "Cloud Deploy (synthetic demo fixture)",
                "severity": "INFO",
                "revision": "payment-api-00042",
                "active_revision": "payment-api-00042",
                "traffic_percent": 100,
                "previous_revision_status": "healthy",
                "verification_status": "verified",
            },
        },
        {
            "event_type": "health_check",
            "service": "payment-api",
            "description": "payment-api readiness check fails on revision payment-api-00042 because application startup cannot parse DATABASE_URL (DATABASE_URL_INVALID).",
            "timestamp": stamp(13),
            "evidence": {
                "source": "Cloud Monitoring (synthetic demo fixture)",
                "severity": "ERROR",
                "revision": "payment-api-00042",
                "readiness": "failed",
                "error_code": "DATABASE_URL_INVALID",
                "verification_status": "verified",
            },
        },
    ]

    persisted = []
    for i, event in enumerate(events, 1):
        result = request_json(base, f"/incidents/{incident_id}/events", "POST", event)
        persisted.append(result)
        print(f"Added event {i}/{len(events)}: {event['event_type']} — {event['service']}")

    # Ensure the canonical service graph includes payment-api -> payment-db/cache.
    # Existing relationships are preserved; POST uses the API's declared model.
    dependencies = [
        {
            "source": "payment-api",
            "target": "payment-db",
            "relationship_type": "database_connection",
            "evidence": "SYNTHETIC DEMO FIXTURE: payment-api requires PostgreSQL via DATABASE_URL; health checks in this incident show payment-db itself is healthy.",
            "confidence": "high",
            "verification_status": "configured",
            "environment": "production",
            "metadata": {"demo_fixture": True},
        },
        {
            "source": "payment-api",
            "target": "payment-cache",
            "relationship_type": "cache_lookup",
            "evidence": "SYNTHETIC DEMO FIXTURE: payment-api uses payment-cache; health checks in this incident show the cache is healthy.",
            "confidence": "high",
            "verification_status": "configured",
            "environment": "production",
            "metadata": {"demo_fixture": True},
        },
    ]
    for dep in dependencies:
        try:
            result = request_json(base, "/dependencies", "POST", dep)
            print("Dependency ensured:", dep["source"], "->", dep["target"], result)
        except RuntimeError as exc:
            # A duplicate unique relationship can be rejected; canonical defaults may already exist.
            if "409" in str(exc) or "already exists" in str(exc).lower():
                print("Dependency already exists:", dep["source"], "->", dep["target"])
            else:
                raise

    print("\nCorrelating persisted events...")
    corr = request_json(base, f"/incidents/{incident_id}/correlated-events?lookback_minutes=720")
    print("Correlation counts:", {
        k: corr.get(k) for k in (
            "total_events", "direct_matches_count", "dependency_related_count",
            "temporally_associated_count", "unrelated_count"
        )
    })

    print("\nRefreshing root-cause analysis...")
    rca = request_json(base, f"/incidents/{incident_id}/root-cause-analysis", "POST", {})
    print(json.dumps({
        "incident_id": rca.get("incident_id"),
        "status": rca.get("status"),
        "insufficient_evidence": rca.get("insufficient_evidence"),
        "summary": rca.get("summary"),
        "evidence_evaluated_count": rca.get("evidence_evaluated_count"),
        "analysis_mode": rca.get("analysis_mode"),
        "hypotheses": [
            {
                "rank": h.get("rank"),
                "explanation": h.get("explanation"),
                "suspected_service": h.get("suspected_service"),
                "suspected_component": h.get("suspected_component"),
                "confidence": h.get("confidence"),
                "supporting_evidence_ids": h.get("supporting_evidence_ids"),
            } for h in (rca.get("hypotheses") or [])
        ],
    }, indent=2))

    print("\nRefreshing causal graph...")
    graph = request_json(base, f"/incidents/{incident_id}/causal-graph/refresh", "POST", {})
    print("Causal graph refreshed. Keys:", list(graph.keys()))

    print("\nIncident events persisted:", len(persisted))
    print("Demo incident ID:", incident_id)
    print("Open the dashboard and select this incident.")
    if rca.get("insufficient_evidence") or not rca.get("hypotheses"):
        print(
            "\nWARNING: the deployed RCA still reports insufficient evidence. "
            "Inspect the correlated-events output and Cloud Run logs; this script does not "
            "force the engine to accept a conclusion."
        )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        sys.exit(1)
