"""Script to safely reset local SQLite development test data and seed 10 realistic edge-case incident scenarios.

DATA SAFETY ENFORCEMENT:
1. Targets LOCAL SQLite database ONLY (backend/tracewell_local.sqlite3).
2. Explicitly aborts if Cloud Firestore or GCP_PROJECT is active.
3. Does NOT delete or modify GCP resources, Cloud Run services, Cloud Logging, or Firestore.
4. Does NOT delete the SQLite file or change its schema.
5. Creates a timestamped backup before any delete operations.
6. Cleans ONLY incident-owned records (incidents, steps, operational_events, correlated_events, root_cause_analyses, investigation_graphs).
7. Preserves shared service catalog and service dependency tables.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.config import GCP_PROJECT, LOCAL_FALLBACK
from backend.store import (
    _connect_local,
    _local_db_path,
    append_operational_event,
    create_local_incident,
    firestore_available,
    get_incident,
    list_operational_events,
)

logger = logging.getLogger("tracewell.reset_and_seed")


# Definition of the 10 realistic incident scenarios
SCENARIOS: list[dict[str, Any]] = [
    {
        "id": "inc-scen-01-pay-deploy-db",
        "title": "Payment API database connection failures after deployment",
        "service": "payment-api",
        "severity": "SEV1",
        "category": "database",
        "description": "Payment API database connection failures and timeout errors spiking immediately following the deployment of revision payment-api-00042-dbconn.",
        "metadata": {
            "is_test_fixture": True,
            "scenario_index": 1,
            "scenario_type": "DEPLOYMENT_RELATED_DATABASE_FAILURE",
        },
        "created_at": "2026-10-17T11:45:00Z",
        "events": [
            {
                "event_type": "deployment",
                "service": "payment-api",
                "description": "Deployment of revision payment-api-00042-dbconn completed with 100% traffic allocation.",
                "timestamp": "2026-10-17T11:32:00Z",
                "evidence": {
                    "revision": "payment-api-00042-dbconn",
                    "image": "gcr.io/tracewell/payment-api:v2.4.1",
                    "deployed_by": "ci-cd-pipeline",
                    "traffic_percent": 100,
                },
                "dedupe_key": "ev-scen-01-dep",
            },
            {
                "event_type": "error_log",
                "service": "payment-api",
                "description": "FATAL: connection timeout to payment-db (postgres-primary.internal:5432) after 30000ms: pool exhausted.",
                "timestamp": "2026-10-17T11:38:00Z",
                "evidence": {
                    "error": "ConnectionTimeoutError",
                    "db_host": "postgres-primary.internal",
                    "port": 5432,
                    "pool_size": 20,
                    "active_connections": 20,
                    "revision": "payment-api-00042-dbconn",
                },
                "dedupe_key": "ev-scen-01-err1",
            },
            {
                "event_type": "error_log",
                "service": "payment-api",
                "description": "HTTP 500: DatabaseConnectionError in POST /v1/charges - failed to acquire connection from pool.",
                "timestamp": "2026-10-17T11:40:00Z",
                "evidence": {
                    "status_code": 500,
                    "endpoint": "/v1/charges",
                    "trace_id": "tr-pay-01-9988",
                    "revision": "payment-api-00042-dbconn",
                },
                "dedupe_key": "ev-scen-01-err2",
            },
            {
                "event_type": "health_check",
                "service": "payment-api",
                "description": "GET /healthz HTTP 200 OK (container liveness probe passed, memory 38% utilized).",
                "timestamp": "2026-10-17T11:42:00Z",
                "evidence": {
                    "status_code": 200,
                    "probe": "liveness",
                    "latency_ms": 5,
                    "note": "Contradictory signal: container is alive but DB connection pool is exhausted.",
                },
                "dedupe_key": "ev-scen-01-hc",
            },
        ],
    },
    {
        "id": "inc-scen-02-ord-upstream-db",
        "title": "Order processing failures caused by database unavailability",
        "service": "order-service",
        "severity": "SEV1",
        "category": "database",
        "description": "Order processing and customer checkout transactions are failing because the upstream database orders-db is unreachable and refusing connections.",
        "metadata": {
            "is_test_fixture": True,
            "scenario_index": 2,
            "scenario_type": "UPSTREAM_DATABASE_OUTAGE",
        },
        "created_at": "2026-10-17T11:40:00Z",
        "events": [
            {
                "event_type": "error_log",
                "service": "orders-db",
                "description": "FATAL: server closed connection unexpectedly: Connection reset by peer on port 5432 (direct observation).",
                "timestamp": "2026-10-17T11:30:00Z",
                "evidence": {
                    "observation_type": "direct_observation",
                    "service": "orders-db",
                    "state": "connection_refused",
                    "port": 5432,
                    "severity": "ERROR",
                },
                "dedupe_key": "ev-scen-02-db-err",
            },
            {
                "event_type": "error_log",
                "service": "order-service",
                "description": "HTTP 500 Internal Server Error: failed to commit order transaction due to database connection refusal on orders-db.",
                "timestamp": "2026-10-17T11:34:00Z",
                "evidence": {
                    "observation_type": "direct_observation",
                    "error": "DatabaseUnavailable",
                    "target": "orders-db",
                    "endpoint": "/orders/checkout",
                    "trace_id": "tr-ord-02-1144",
                },
                "dedupe_key": "ev-scen-02-ord-err",
            },
            {
                "event_type": "error_log",
                "service": "order-service",
                "description": "Order submission queue backlog exceeded threshold (540 pending jobs) due to blocked database commits (inferred impact).",
                "timestamp": "2026-10-17T11:36:00Z",
                "evidence": {
                    "observation_type": "inferred_impact",
                    "backlog_count": 540,
                    "metric": "queue_depth",
                    "impact_classification": "symptomatic_backlog",
                },
                "dedupe_key": "ev-scen-02-ord-backlog",
            },
        ],
    },
    {
        "id": "inc-scen-03-pay-downstream-cascade",
        "title": "Checkout failures following payment service degradation",
        "service": "payment-api",
        "severity": "SEV1",
        "category": "latency",
        "description": "Payment API latency spike and gateway timeouts causing cascading transaction failures across downstream order processing and checkout services.",
        "metadata": {
            "is_test_fixture": True,
            "scenario_index": 3,
            "scenario_type": "DOWNSTREAM_CASCADING_FAILURE",
        },
        "created_at": "2026-10-17T11:35:00Z",
        "events": [
            {
                "event_type": "error_log",
                "service": "payment-api",
                "description": "payment-api latency spike p99 > 5200ms with HTTP 504 Gateway Timeout during charge settlement (root cause candidate).",
                "timestamp": "2026-10-17T11:20:00Z",
                "evidence": {
                    "p99_latency_ms": 5240,
                    "status_code": 504,
                    "role": "root_cause_candidate",
                    "severity": "ERROR",
                },
                "dedupe_key": "ev-scen-03-pay-err",
            },
            {
                "event_type": "error_log",
                "service": "order-service",
                "description": "HTTP 502 Bad Gateway: downstream order-service call to payment-api timed out after 5000ms (downstream symptom).",
                "timestamp": "2026-10-17T11:22:00Z",
                "evidence": {
                    "status_code": 502,
                    "target": "payment-api",
                    "role": "downstream_symptom",
                    "severity": "ERROR",
                },
                "dedupe_key": "ev-scen-03-ord-err",
            },
            {
                "event_type": "error_log",
                "service": "frontend-checkout",
                "description": "HTTP 500: Customer checkout flow failed - payment service unreachable via order-service (cascading symptom).",
                "timestamp": "2026-10-17T11:25:00Z",
                "evidence": {
                    "status_code": 500,
                    "target": "order-service",
                    "role": "downstream_symptom",
                    "severity": "ERROR",
                },
                "dedupe_key": "ev-scen-03-front-err",
            },
        ],
    },
    {
        "id": "inc-scen-04-auth-no-deploy-err",
        "title": "Elevated application errors without a recent deployment",
        "service": "auth-service",
        "severity": "SEV2",
        "category": "application",
        "description": "Elevated authentication token validation errors observed in production with no matching deployment or configuration change in the investigation window.",
        "metadata": {
            "is_test_fixture": True,
            "scenario_index": 4,
            "scenario_type": "NO_DEPLOYMENT_APPLICATION_ERRORS",
        },
        "created_at": "2026-10-17T11:30:00Z",
        "events": [
            {
                "event_type": "error_log",
                "service": "auth-service",
                "description": "JWT signature verification failed: invalid token issuer or key rotation mismatch for tenant-id: acme-corp.",
                "timestamp": "2026-10-17T11:15:00Z",
                "evidence": {
                    "error": "JWTVerificationError",
                    "tenant": "acme-corp",
                    "key_id": "key-2026-rot",
                    "token_payload": "[obfuscated]",
                    "severity": "ERROR",
                },
                "dedupe_key": "ev-scen-04-auth-err1",
            },
            {
                "event_type": "error_log",
                "service": "auth-service",
                "description": "HTTP 401 Unauthorized: token expired or signature invalid during oauth introspect.",
                "timestamp": "2026-10-17T11:18:00Z",
                "evidence": {
                    "status_code": 401,
                    "endpoint": "/oauth/introspect",
                    "error_count": 82,
                    "severity": "ERROR",
                },
                "dedupe_key": "ev-scen-04-auth-err2",
            },
            {
                "event_type": "health_check",
                "service": "auth-service",
                "description": "GET /healthz HTTP 200 OK (container liveness probe passed, memory 42% utilized).",
                "timestamp": "2026-10-17T11:20:00Z",
                "evidence": {
                    "status_code": 200,
                    "probe": "liveness",
                    "memory_pct": 42,
                    "last_deployment_age_days": 14,
                },
                "dedupe_key": "ev-scen-04-auth-hc",
            },
        ],
    },
    {
        "id": "inc-scen-05-pay-conflicting-health",
        "title": "Service reports healthy while requests continue failing",
        "service": "payment-api",
        "severity": "SEV2",
        "category": "application",
        "description": "Service liveness probe reports healthy and responsive while customer charge requests fail with thread pool exhaustion.",
        "metadata": {
            "is_test_fixture": True,
            "scenario_index": 5,
            "scenario_type": "CONFLICTING_HEALTH_SIGNALS",
        },
        "created_at": "2026-10-17T11:25:00Z",
        "events": [
            {
                "event_type": "health_check",
                "service": "payment-api",
                "description": "GET /healthz HTTP 200 OK: shallow liveness probe succeeded (response_time: 4ms).",
                "timestamp": "2026-10-17T11:10:00Z",
                "evidence": {
                    "status_code": 200,
                    "response_time_ms": 4,
                    "probe_type": "shallow_liveness",
                    "note": "Health check verifies process alive but not internal thread pool saturation.",
                },
                "dedupe_key": "ev-scen-05-pay-hc",
            },
            {
                "event_type": "error_log",
                "service": "payment-api",
                "description": "POST /v1/charges HTTP 500: worker thread pool exhausted (50/50 active workers busy).",
                "timestamp": "2026-10-17T11:11:00Z",
                "evidence": {
                    "active_threads": 50,
                    "max_threads": 50,
                    "queue_size": 200,
                    "status_code": 500,
                    "severity": "ERROR",
                },
                "dedupe_key": "ev-scen-05-pay-err1",
            },
            {
                "event_type": "error_log",
                "service": "payment-api",
                "description": "RejectedExecutionException: ThreadPoolExecutor task capacity limit reached on worker pool.",
                "timestamp": "2026-10-17T11:13:00Z",
                "evidence": {
                    "exception": "RejectedExecutionException",
                    "dropped_requests": 34,
                    "severity": "ERROR",
                },
                "dedupe_key": "ev-scen-05-pay-err2",
            },
        ],
    },
    {
        "id": "inc-scen-06-pay-unrelated-notif",
        "title": "Payment errors coincide with unrelated notification failures",
        "service": "payment-api",
        "severity": "SEV2",
        "category": "application",
        "description": "Payment API token validation errors coincide in time with SMTP connection failures on notification-service, but the services share no dependency relationship.",
        "metadata": {
            "is_test_fixture": True,
            "scenario_index": 6,
            "scenario_type": "TEMPORALLY_RELATED_UNRELATED_SERVICE",
        },
        "created_at": "2026-10-17T11:20:00Z",
        "events": [
            {
                "event_type": "error_log",
                "service": "payment-api",
                "description": "HTTP 401: OAuth token validation timeout against internal auth provider.",
                "timestamp": "2026-10-17T11:05:00Z",
                "evidence": {
                    "status_code": 401,
                    "endpoint": "/v1/tokens/verify",
                    "service": "payment-api",
                    "severity": "ERROR",
                },
                "dedupe_key": "ev-scen-06-pay-err",
            },
            {
                "event_type": "error_log",
                "service": "notification-service",
                "description": "SMTP connection refused to sendgrid.net:587 after 3 retries (notification batch delayed).",
                "timestamp": "2026-10-17T11:06:00Z",
                "evidence": {
                    "smtp_host": "sendgrid.net",
                    "port": 587,
                    "retries": 3,
                    "service": "notification-service",
                    "relationship_to_payment_api": "none",
                    "severity": "ERROR",
                },
                "dedupe_key": "ev-scen-06-notif-err",
            },
        ],
    },
    {
        "id": "inc-scen-07-pay-dup-log-ingest",
        "title": "Repeated ingestion of the same application error",
        "service": "payment-api",
        "severity": "SEV3",
        "category": "logging",
        "description": "Multiple ingestion attempts referencing the same underlying log event, accompanied by a genuinely separate distinct error event.",
        "metadata": {
            "is_test_fixture": True,
            "scenario_index": 7,
            "scenario_type": "DUPLICATE_LOG_INGESTION",
        },
        "created_at": "2026-10-17T11:15:00Z",
        "events": [
            {
                "event_type": "error_log",
                "service": "payment-api",
                "description": "Database connection reset by peer on payment-api-worker-7 (stable dedupe key log-dup-err-998877).",
                "timestamp": "2026-10-17T11:00:00Z",
                "evidence": {
                    "insert_id": "log-dup-err-998877",
                    "worker": "payment-api-worker-7",
                    "code": "ECONNRESET",
                    "severity": "ERROR",
                },
                "dedupe_key": "log-dup-err-998877",
            },
            {
                "event_type": "error_log",
                "service": "payment-api",
                "description": "Memory usage warning: heap size exceeded 85% threshold on payment-api-worker-2 (distinct error event).",
                "timestamp": "2026-10-17T11:02:00Z",
                "evidence": {
                    "insert_id": "log-distinct-err-112233",
                    "worker": "payment-api-worker-2",
                    "heap_pct": 87,
                    "severity": "WARNING",
                },
                "dedupe_key": "log-distinct-err-112233",
            },
        ],
    },
    {
        "id": "inc-scen-08-ord-missing-revision",
        "title": "Service degradation with incomplete deployment metadata",
        "service": "order-service",
        "severity": "SEV2",
        "category": "deployment",
        "description": "Service degradation observed where error logs lack revision identifiers and deployment metadata is incomplete.",
        "metadata": {
            "is_test_fixture": True,
            "scenario_index": 8,
            "scenario_type": "MISSING_REVISION_INCOMPLETE_EVIDENCE",
        },
        "created_at": "2026-10-17T11:10:00Z",
        "events": [
            {
                "event_type": "error_log",
                "service": "order-service",
                "description": "HTTP 500: Unexpected null pointer exception during order dispatch (missing revision identifier).",
                "timestamp": "2026-10-17T10:50:00Z",
                "evidence": {
                    "revision": None,
                    "commit": None,
                    "trace_id": "trace-ord-8831",
                    "status_code": 500,
                    "severity": "ERROR",
                },
                "dedupe_key": "ev-scen-08-ord-err",
            },
            {
                "event_type": "deployment",
                "service": "order-service",
                "description": "Cloud Run deployment registered with incomplete metadata (revision name null, commit untracked).",
                "timestamp": "2026-10-17T10:45:00Z",
                "evidence": {
                    "revision": None,
                    "commit": None,
                    "status": "incomplete_metadata",
                    "source": "unverified_build",
                },
                "dedupe_key": "ev-scen-08-ord-dep",
            },
        ],
    },
    {
        "id": "inc-scen-09-pay-cache-latency",
        "title": "Intermittent payment latency following cache connection failures",
        "service": "payment-api",
        "severity": "SEV2",
        "category": "latency",
        "description": "Intermittent payment processing latency following Redis cache connection timeouts, triggering database query fallbacks.",
        "metadata": {
            "is_test_fixture": True,
            "scenario_index": 9,
            "scenario_type": "NETWORK_CACHE_FAILURE",
        },
        "created_at": "2026-10-17T11:05:00Z",
        "events": [
            {
                "event_type": "error_log",
                "service": "payment-cache",
                "description": "RedisConnectionError: Read timeout to payment-cache (redis-cluster:6379) after 1500ms.",
                "timestamp": "2026-10-17T10:40:00Z",
                "evidence": {
                    "cache_host": "redis-cluster.internal",
                    "port": 6379,
                    "timeout_ms": 1500,
                    "service": "payment-cache",
                    "severity": "ERROR",
                },
                "dedupe_key": "ev-scen-09-cache-err",
            },
            {
                "event_type": "error_log",
                "service": "payment-api",
                "description": "Payment transaction latency spike: p99 degraded from 85ms to 4200ms due to cache-miss fallback load.",
                "timestamp": "2026-10-17T10:42:00Z",
                "evidence": {
                    "p99_latency_ms": 4200,
                    "normal_p99_ms": 85,
                    "reason": "cache_miss_db_fallback",
                    "severity": "WARNING",
                },
                "dedupe_key": "ev-scen-09-pay-lat",
            },
        ],
    },
    {
        "id": "inc-scen-10-auth-insufficient-telemetry",
        "title": "Service degradation with no actionable telemetry",
        "service": "auth-service",
        "severity": "SEV3",
        "category": "telemetry",
        "description": "Customer reports of intermittent service degradation with no actionable telemetry, errors, or deployment records in the incident window.",
        "metadata": {
            "is_test_fixture": True,
            "scenario_index": 10,
            "scenario_type": "INSUFFICIENT_EVIDENCE",
        },
        "created_at": "2026-10-17T11:00:00Z",
        "events": [],
    },
]


def ensure_scenario_services_and_dependencies() -> None:
    """Ensure catalog services and dependencies used by scenarios exist."""
    required_services = [
        {
            "name": "orders-db",
            "service_type": "database",
            "environment": "production",
            "health_status": "healthy",
            "source": "configured",
            "description": "Cloud SQL PostgreSQL instance storing order and checkout transaction records.",
        },
        {
            "name": "inventory-service",
            "service_type": "cloud_run",
            "environment": "production",
            "health_status": "healthy",
            "source": "configured",
            "description": "Inventory management and stock allocation service.",
        },
        {
            "name": "frontend-checkout",
            "service_type": "web_app",
            "environment": "production",
            "health_status": "healthy",
            "source": "configured",
            "description": "Customer checkout frontend single-page application.",
        },
    ]

    required_dependencies = [
        {
            "id": "dep-ord-orders-db",
            "source": "order-service",
            "target": "orders-db",
            "source_service": "order-service",
            "target_service": "orders-db",
            "relationship_type": "database_connection",
            "evidence": "Configured database host in order-service connection pool spec.",
            "confidence": "high",
            "verification_status": "verified",
        },
        {
            "id": "dep-ord-inventory",
            "source": "order-service",
            "target": "inventory-service",
            "source_service": "order-service",
            "target_service": "inventory-service",
            "relationship_type": "sync_http",
            "evidence": "Synchronous HTTP client for order reservation.",
            "confidence": "high",
            "verification_status": "configured",
        },
        {
            "id": "dep-front-order",
            "source": "frontend-checkout",
            "target": "order-service",
            "source_service": "frontend-checkout",
            "target_service": "order-service",
            "relationship_type": "sync_http",
            "evidence": "Web checkout API integration calling order creation endpoint.",
            "confidence": "high",
            "verification_status": "configured",
        },
    ]

    now_iso = datetime.now(timezone.utc).isoformat()
    with _connect_local() as conn:
        for svc in required_services:
            conn.execute(
                """
                INSERT OR IGNORE INTO services (name, service_type, environment, health_status, source, payload, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    svc["name"],
                    svc["service_type"],
                    svc["environment"],
                    svc["health_status"],
                    svc["source"],
                    json.dumps(svc),
                    now_iso,
                ),
            )

        for dep in required_dependencies:
            conn.execute(
                """
                INSERT OR IGNORE INTO service_dependencies (
                    id, source_service, target_service, relationship_type, evidence, confidence, verification_status, payload, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    dep["id"],
                    dep["source_service"],
                    dep["target_service"],
                    dep["relationship_type"],
                    dep["evidence"],
                    dep["confidence"],
                    dep["verification_status"],
                    json.dumps(dep),
                    now_iso,
                ),
            )
        conn.commit()


def reset_and_seed_local_test_data() -> dict[str, Any]:
    """Safely reset local development incidents and seed 10 realistic edge-case scenarios."""
    # 1. Verify safety preconditions
    if GCP_PROJECT or firestore_available():
        raise RuntimeError(
            "ABORTED: Refusing to reset data. Active persistence mode indicates Cloud Firestore is configured. "
            "Data reset is strictly restricted to local SQLite development mode."
        )

    db_path = _local_db_path()
    if not db_path.exists():
        raise FileNotFoundError(f"Local SQLite database not found at {db_path}")

    # 2. Create timestamped backup of local SQLite database
    timestamp_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    backup_path = db_path.parent / f"{db_path.name}.backup_{timestamp_str}"
    shutil.copy2(db_path, backup_path)
    logger.info("Created local SQLite backup at: %s", backup_path)

    # 3. Clean incident-owned tables only
    with _connect_local() as conn:
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("DELETE FROM correlated_events")
        conn.execute("DELETE FROM investigation_graphs")
        conn.execute("DELETE FROM root_cause_analyses")
        conn.execute("DELETE FROM steps")
        conn.execute("DELETE FROM operational_events")
        conn.execute("DELETE FROM incidents")
        conn.commit()

    # 4. Ensure service dependencies catalog contains required entries
    ensure_scenario_services_and_dependencies()

    # 5. Seed the 10 scenarios
    seeded_incidents: list[dict[str, Any]] = []
    for sc in SCENARIOS:
        incident_id = sc["id"]
        fields = {
            "title": sc["title"],
            "service": sc["service"],
            "description": sc["description"],
            "severity": sc["severity"],
            "category": sc["category"],
            "source": "human",
            "status": "open",
            "attachments": [],
            "created_at": sc["created_at"],
            "metadata": sc["metadata"],
        }
        create_local_incident(incident_id, fields)

        # Seed operational events
        for ev in sc.get("events", []):
            append_operational_event(
                incident_id,
                event_type=ev["event_type"],
                service=ev["service"],
                description=ev["description"],
                event_timestamp=ev["timestamp"],
                evidence=ev["evidence"],
                dedupe_key=ev["dedupe_key"],
            )

        seeded_incidents.append({"id": incident_id, "title": sc["title"], "service": sc["service"], "severity": sc["severity"]})

    return {
        "status": "success",
        "backup_path": str(backup_path),
        "db_path": str(db_path),
        "seeded_count": len(seeded_incidents),
        "incidents": seeded_incidents,
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("Starting local test data reset...")
    res = reset_and_seed_local_test_data()
    print(f"Backup created at: {res['backup_path']}")
    print(f"Successfully seeded {res['seeded_count']} realistic incidents:")
    for inc in res["incidents"]:
        print(f"  [{inc['severity']}] {inc['id']}: {inc['title']} ({inc['service']})")
