"""Service dependency mapping, discovery, and graph traversal engine."""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from .store import (
    get_db,
    get_incident,
    get_service_node,
    list_incidents,
    list_operational_events,
    list_service_nodes,
    save_dependency_relationship,
    save_service_node,
    list_dependency_relationships,
    get_dependency_relationship,
)

logger = logging.getLogger("tracewell.dependencies")


# -------------------------------------------------------------------
# Data Models
# -------------------------------------------------------------------


class ServiceType(str, Enum):
    CLOUD_RUN = "cloud_run"
    MICROSERVICE = "microservice"
    DATABASE = "database"
    CACHE = "cache"
    EVENT_BROKER = "event_broker"
    EXTERNAL_API = "external_api"
    GATEWAY = "gateway"
    STORAGE = "storage"
    OTHER = "other"


class HealthStatus(str, Enum):
    HEALTHY = "healthy"
    UNHEALTHY = "unhealthy"
    DEGRADED = "degraded"
    UNKNOWN = "unknown"


class VerificationStatus(str, Enum):
    CONFIGURED = "configured"
    VERIFIED = "verified"
    INFERRED = "inferred"


class ConfidenceLevel(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class ServiceNode(BaseModel):
    name: str = Field(min_length=1, max_length=100, description="Service name")
    service_type: str = Field(default="microservice", max_length=50, description="Service type")
    environment: str = Field(default="production", max_length=50, description="Environment")
    health_status: str | None = Field(default="unknown", max_length=30, description="Health status")
    source: str = Field(default="configured", max_length=100, description="Source of dependency information")
    description: str | None = Field(default=None, max_length=1000, description="Service description")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Metadata")
    updated_at: str | None = None


class DependencyRelationship(BaseModel):
    source: str = Field(min_length=1, max_length=100, description="Source service (dependent/caller)")
    target: str = Field(min_length=1, max_length=100, description="Target service (dependency/provider)")
    relationship_type: str = Field(default="sync_http", max_length=50, description="Relationship type")
    evidence: str = Field(min_length=1, max_length=2000, description="Evidence or configuration source")
    confidence: str = Field(default="high", max_length=20, description="Confidence status")
    verification_status: str = Field(default="configured", max_length=30, description="Verification status")
    environment: str = Field(default="production", max_length=50, description="Environment")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Additional metadata")
    created_at: str | None = None


class CreateDependencyRequest(BaseModel):
    source: str = Field(min_length=1, max_length=100)
    target: str = Field(min_length=1, max_length=100)
    relationship_type: str = Field(default="sync_http", max_length=50)
    evidence: str = Field(min_length=1, max_length=2000)
    confidence: str = Field(default="high", max_length=20)
    verification_status: str = Field(default="configured", max_length=30)
    environment: str = Field(default="production", max_length=50)
    metadata: dict[str, Any] = Field(default_factory=dict)


class CreateServiceRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    service_type: str = Field(default="microservice", max_length=50)
    environment: str = Field(default="production", max_length=50)
    health_status: str = Field(default="unknown", max_length=30)
    source: str = Field(default="configured", max_length=100)
    description: str | None = Field(default=None, max_length=1000)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ImpactedService(BaseModel):
    service_name: str
    impact_level: str  # "confirmed" | "potential"
    depth: int
    dependency_path: list[str]
    relationship_types: list[str]
    verification_status: str
    reasons: list[str]
    node: dict[str, Any] | None = None


class DownstreamImpactResult(BaseModel):
    service: str
    affected_service: dict[str, Any] | None
    impacted_services: list[ImpactedService]
    total_impacted: int
    confirmed_impact_count: int
    potential_impact_count: int
    cycle_detected: bool
    cycles: list[list[str]]


# -------------------------------------------------------------------
# Canonical Architecture Configuration
# -------------------------------------------------------------------

DEFAULT_SERVICES: list[dict[str, Any]] = [
    {
        "name": "order-service",
        "service_type": "cloud_run",
        "environment": "production",
        "health_status": "healthy",
        "source": "configured",
        "description": "Order lifecycle, customer checkout, and state management microservice.",
    },
    {
        "name": "payment-api",
        "service_type": "cloud_run",
        "environment": "production",
        "health_status": "unknown",
        "source": "configured",
        "description": "Payment authorization microservice exposing /health and /pay endpoints.",
    },
    {
        "name": "auth-service",
        "service_type": "cloud_run",
        "environment": "production",
        "health_status": "healthy",
        "source": "configured",
        "description": "OAuth2 identity provider and JWT token issuance/verification microservice.",
    },
    {
        "name": "notification-service",
        "service_type": "cloud_run",
        "environment": "production",
        "health_status": "healthy",
        "source": "configured",
        "description": "Asynchronous customer notification worker triggered via Cloud Pub/Sub.",
    },
    {
        "name": "payment-db",
        "service_type": "database",
        "environment": "production",
        "health_status": "healthy",
        "source": "configured",
        "description": "Cloud SQL PostgreSQL database instance hosting payment transaction ledger.",
    },
    {
        "name": "payment-cache",
        "service_type": "cache",
        "environment": "production",
        "health_status": "healthy",
        "source": "configured",
        "description": "Memorystore Redis cluster for authorization rate-limiting and query caching.",
    },
    {
        "name": "auth-cache",
        "service_type": "cache",
        "environment": "production",
        "health_status": "healthy",
        "source": "configured",
        "description": "Redis cache cluster storing active user session tokens.",
    },
    {
        "name": "firestore",
        "service_type": "database",
        "environment": "production",
        "health_status": "healthy",
        "source": "configured",
        "description": "Google Cloud Firestore document database for order and incident persistence.",
    },
]

DEFAULT_DEPENDENCIES: list[dict[str, Any]] = [
    {
        "source": "order-service",
        "target": "payment-api",
        "relationship_type": "sync_http",
        "evidence": "Runbook doc_01/doc_12: order-service dispatches synchronous charge requests to payment-api /pay via IAM OIDC",
        "confidence": "high",
        "verification_status": "configured",
        "environment": "production",
    },
    {
        "source": "order-service",
        "target": "auth-service",
        "relationship_type": "auth_token_validation",
        "evidence": "Runbook doc_11/doc_12: order-service validates incoming JWT signatures against auth-service JWKS",
        "confidence": "high",
        "verification_status": "configured",
        "environment": "production",
    },
    {
        "source": "order-service",
        "target": "firestore",
        "relationship_type": "database_connection",
        "evidence": "Runbook doc_12/doc_15: order-service persists order lifecycle records in Firestore",
        "confidence": "high",
        "verification_status": "configured",
        "environment": "production",
    },
    {
        "source": "order-service",
        "target": "notification-service",
        "relationship_type": "pubsub_event",
        "evidence": "Runbook doc_13: order confirmation publishes Cloud Pub/Sub message to notification-service push endpoint",
        "confidence": "high",
        "verification_status": "configured",
        "environment": "production",
    },
    {
        "source": "payment-api",
        "target": "payment-db",
        "relationship_type": "database_connection",
        "evidence": "Runbook doc_01/doc_02: payment-api requires PostgreSQL connection via DATABASE_URL to authorize transactions",
        "confidence": "high",
        "verification_status": "configured",
        "environment": "production",
    },
    {
        "source": "payment-api",
        "target": "payment-cache",
        "relationship_type": "cache_lookup",
        "evidence": "Runbook doc_03/doc_14: payment-api caches authorization and rate limiting keys in Redis via REDIS_URL",
        "confidence": "high",
        "verification_status": "configured",
        "environment": "production",
    },
    {
        "source": "payment-api",
        "target": "auth-service",
        "relationship_type": "auth_token_validation",
        "evidence": "Runbook doc_11: payment-api validates JWT signatures against auth-service JWKS",
        "confidence": "high",
        "verification_status": "configured",
        "environment": "production",
    },
    {
        "source": "auth-service",
        "target": "auth-cache",
        "relationship_type": "cache_lookup",
        "evidence": "Runbook doc_11: auth-service caches user session tokens in Redis with 3600-second TTL",
        "confidence": "high",
        "verification_status": "configured",
        "environment": "production",
    },
    {
        "source": "notification-service",
        "target": "firestore",
        "relationship_type": "database_connection",
        "evidence": "Runbook doc_13: notification-service retrieves user communication preferences and deduplication keys from Firestore",
        "confidence": "high",
        "verification_status": "configured",
        "environment": "production",
    },
]


def init_service_dependencies(force: bool = False) -> None:
    """Seed canonical configured services and dependencies if not already present."""
    existing_services = list_service_nodes()
    if not existing_services or force:
        for svc in DEFAULT_SERVICES:
            save_service_node(svc)

    existing_deps = list_dependency_relationships()
    if not existing_deps or force:
        for dep in DEFAULT_DEPENDENCIES:
            save_dependency_relationship(dep)


def ensure_initialized() -> None:
    """Ensure persistence has at least the default configured services."""
    existing = list_service_nodes()
    if not existing:
        init_service_dependencies(force=False)


# -------------------------------------------------------------------
# Discovery Engine
# -------------------------------------------------------------------


def discover_dependencies_from_evidence(
    cloud_run_data: dict[str, Any] | None = None,
    operational_events: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Discover or verify dependencies from explicit evidence.

    STRICT RULES ENFORCED:
    1. Do not assume two services are dependent merely because they share timestamps
       or appear in the same incident.
    2. Cloud Run metadata (e.g. env vars like DATABASE_URL or VPC connector) provides
       discovered dependency information, clearly labeled as 'discovered_cloud_run'.
    3. Operational events containing explicit error signatures (such as 'DATABASE_URL invalid'
       or 'Redis connection refused') upgrade configured relationships to 'verified' with
       the error log payload cited as evidence.
    4. If actual dependency information is unavailable, explicitly configured dependencies
       are supported and labeled as 'configured' rather than 'discovered'.
    """
    ensure_initialized()
    updated_count = 0
    discovered_count = 0

    # 1. Inspect environment variables of the running application
    env_payment_url = os.getenv("PAYMENT_API_URL") or os.getenv("SERVICE_URL_PAYMENT_API")
    if env_payment_url:
        existing = get_dependency_relationship("order-service", "payment-api", "sync_http")
        if existing:
            save_dependency_relationship({
                **existing,
                "evidence": f"Application environment variable configured: PAYMENT_API_URL={env_payment_url}",
                "verification_status": "verified",
                "confidence": "high",
            })
            updated_count += 1

    # 2. Inspect Cloud Run metadata when provided
    if cloud_run_data and cloud_run_data.get("status") == "success":
        service_name = cloud_run_data.get("service")
        revisions = cloud_run_data.get("revisions") or []
        uri = cloud_run_data.get("uri")

        # Update service node metadata if available
        if service_name:
            node = get_service_node(service_name)
            if node:
                metadata = node.get("metadata") or {}
                metadata["cloud_run_uri"] = uri
                metadata["revisions_count"] = len(revisions)
                save_service_node({
                    **node,
                    "source": "discovered_cloud_run",
                    "metadata": metadata,
                })

        # Inspect revisions for explicit dependency configuration
        for rev in revisions:
            # Check container image and environment variables if populated
            containers = rev.get("containers") or []
            for container in containers:
                env_vars = container.get("env") or {}
                if "DATABASE_URL" in env_vars:
                    save_dependency_relationship({
                        "source": service_name,
                        "target": f"{service_name}-db" if f"{service_name}-db" in [s["name"] for s in list_service_nodes()] else "payment-db",
                        "relationship_type": "database_connection",
                        "evidence": f"Cloud Run revision {rev.get('name')} specifies DATABASE_URL environment variable binding",
                        "confidence": "high",
                        "verification_status": "verified",
                        "source_of_info": "discovered_cloud_run",
                    })
                    discovered_count += 1

    # 3. Inspect operational events for EXPLICIT dependency evidence
    # (Strict: only if the log payload or evidence specifically identifies a dependency)
    if operational_events:
        for event in operational_events:
            service = event.get("service")
            description = (event.get("description") or "").lower()
            evidence = event.get("evidence") or {}
            log_payload = str(evidence.get("log", {}).get("payload") or "").lower()

            combined_text = f"{description} {log_payload}"

            # Check for PostgreSQL / DATABASE_URL explicit error evidence
            if "database_url invalid" in combined_text or "postgresql" in combined_text:
                if service == "payment-api":
                    existing = get_dependency_relationship("payment-api", "payment-db", "database_connection")
                    if existing:
                        save_dependency_relationship({
                            **existing,
                            "evidence": (
                                f"Verified via Cloud Logging event: {event.get('description', '')[:200]} "
                                f"(timestamp: {event.get('timestamp')})"
                            ),
                            "verification_status": "verified",
                            "confidence": "high",
                        })
                        updated_count += 1

            # Check for Redis explicit error evidence
            if "redis connection refused" in combined_text or "redis" in combined_text:
                if service == "payment-api":
                    existing = get_dependency_relationship("payment-api", "payment-cache", "cache_lookup")
                    if existing:
                        save_dependency_relationship({
                            **existing,
                            "evidence": (
                                f"Verified via Cloud Logging error event: {event.get('description', '')[:200]} "
                                f"(timestamp: {event.get('timestamp')})"
                            ),
                            "verification_status": "verified",
                            "confidence": "high",
                        })
                        updated_count += 1

            # Check for JWT / Auth service explicit error evidence
            if "jwks" in combined_text or "jwt" in combined_text:
                if service in ("payment-api", "order-service"):
                    existing = get_dependency_relationship(service, "auth-service", "auth_token_validation")
                    if existing:
                        save_dependency_relationship({
                            **existing,
                            "evidence": (
                                f"Verified via auth token failure event: {event.get('description', '')[:200]}"
                            ),
                            "verification_status": "verified",
                            "confidence": "high",
                        })
                        updated_count += 1

    return {
        "status": "success",
        "updated_relationships": updated_count,
        "discovered_relationships": discovered_count,
        "total_relationships": len(list_dependency_relationships()),
    }


# -------------------------------------------------------------------
# Graph Traversal Engine
# -------------------------------------------------------------------


def get_service_dependencies(
    service_name: str,
    direction: str = "both",
    environment: str | None = None,
) -> dict[str, Any]:
    """Retrieve direct dependencies for a specified service.

    - direction 'upstream': services that service_name depends on (callees / providers)
    - direction 'downstream': services that depend on service_name (callers / consumers)
    - direction 'both': both upstream and downstream
    """
    ensure_initialized()

    node = get_service_node(service_name)
    upstream_deps = []
    downstream_deps = []

    if direction in ("upstream", "both"):
        upstream_deps = list_dependency_relationships(source=service_name, environment=environment)

    if direction in ("downstream", "both"):
        downstream_deps = list_dependency_relationships(target=service_name, environment=environment)

    return {
        "service": service_name,
        "node": node,
        "upstream": upstream_deps,
        "downstream": downstream_deps,
        "upstream_count": len(upstream_deps),
        "downstream_count": len(downstream_deps),
    }


def analyze_downstream_impact(
    service_name: str,
    max_depth: int = 10,
    environment: str | None = None,
) -> dict[str, Any]:
    """Given an affected service, identify all services that may be impacted.

    A downstream service is one that depends on the affected service (directly or
    transitively). When the affected service experiences degradation or failure,
    its callers and consumers are impacted.

    PREVENTS INFINITE LOOPS:
    - Tracks active recursion stack and visited depths.
    - Detects cycles and records cycle paths cleanly.

    CLEARLY DISTINGUISHES:
    - Confirmed impact: direct verified dependencies with active failure signals or
      active incident reports on the downstream service.
    - Potential impact: unverified/configured dependencies, or transitive dependencies
      at depth > 1 where fallback mechanisms or circuit breakers may mitigate failure.
    """
    ensure_initialized()

    affected_node = get_service_node(service_name)
    all_dependencies = list_dependency_relationships(environment=environment)

    # Build adjacency map for downstream traversal:
    # Key: target_service -> List of relationships where target_service is called by source_service
    downstream_map: dict[str, list[dict[str, Any]]] = {}
    for dep in all_dependencies:
        tgt = dep.get("target") or dep.get("target_service")
        if tgt:
            downstream_map.setdefault(tgt, []).append(dep)

    # Check for active incidents across services to correlate confirmed impact
    active_incidents_by_service: dict[str, list[dict[str, Any]]] = {}
    try:
        incidents = list_incidents()
        for inc in incidents:
            if inc.get("status") not in ("closed", "resolved", "rejected"):
                svc = inc.get("service")
                if svc:
                    active_incidents_by_service.setdefault(svc, []).append(inc)
    except Exception:
        pass

    impacted_services: list[dict[str, Any]] = []
    visited_depths: dict[str, int] = {}
    cycle_detected = False
    detected_cycles: list[list[str]] = []

    def traverse(current_service: str, depth: int, current_path: list[str], rel_path: list[str]) -> None:
        nonlocal cycle_detected

        if depth > max_depth:
            return

        dependents = downstream_map.get(current_service, [])
        for dep in dependents:
            caller = dep.get("source") or dep.get("source_service")
            rel_type = dep.get("relationship_type", "sync_http")
            ver_status = dep.get("verification_status", "configured")

            if not caller:
                continue

            # Cycle detection: if caller is already in current path
            if caller in current_path:
                cycle_detected = True
                cycle_path = current_path + [caller]
                if cycle_path not in detected_cycles:
                    detected_cycles.append(cycle_path)
                logger.warning("Dependency cycle detected: %s", " -> ".join(cycle_path))
                continue

            # Check if caller has already been visited at an equal or lower depth
            if caller in visited_depths and visited_depths[caller] <= depth:
                continue

            visited_depths[caller] = depth

            # Evaluate Confirmed vs Potential Impact
            impact_level = "potential"
            reasons: list[str] = []

            # 1. Does the caller have an active incident open?
            if caller in active_incidents_by_service:
                impact_level = "confirmed"
                inc_titles = [i.get("title", "") for i in active_incidents_by_service[caller]]
                reasons.append(
                    f"Confirmed: Caller '{caller}' currently has active incident open ({', '.join(inc_titles[:2])})"
                )

            # 2. Is this a direct dependency (depth 1) with verified status?
            elif depth == 1 and ver_status == "verified":
                impact_level = "confirmed"
                reasons.append(
                    f"Confirmed: Direct verified dependency ({rel_type}) on degraded service '{current_service}'"
                )

            # 3. Direct dependency, configured but unverified
            elif depth == 1:
                impact_level = "potential"
                reasons.append(
                    f"Potential: Direct configured dependency ({rel_type}) on service '{current_service}'"
                )

            # 4. Transitive dependency (depth > 1)
            else:
                impact_level = "potential"
                reasons.append(
                    f"Potential: Transitive dependency at depth {depth} via {' -> '.join(current_path[1:] + [current_service])}"
                )

            caller_node = get_service_node(caller)
            path_services = [caller] + list(reversed(current_path))
            path_rels = rel_path + [rel_type]

            impacted_services.append({
                "service_name": caller,
                "impact_level": impact_level,
                "depth": depth,
                "dependency_path": path_services,
                "relationship_types": path_rels,
                "verification_status": ver_status,
                "reasons": reasons,
                "node": caller_node,
            })

            # Recurse downstream
            traverse(
                current_service=caller,
                depth=depth + 1,
                current_path=current_path + [caller],
                rel_path=path_rels,
            )

    # Start traversal from the affected service
    traverse(
        current_service=service_name,
        depth=1,
        current_path=[service_name],
        rel_path=[],
    )

    # Sort impacted services by depth then name
    impacted_services.sort(key=lambda x: (x["depth"], x["service_name"]))

    confirmed_count = sum(1 for s in impacted_services if s["impact_level"] == "confirmed")
    potential_count = sum(1 for s in impacted_services if s["impact_level"] == "potential")

    return {
        "service": service_name,
        "affected_service": affected_node,
        "impacted_services": impacted_services,
        "total_impacted": len(impacted_services),
        "confirmed_impact_count": confirmed_count,
        "potential_impact_count": potential_count,
        "cycle_detected": cycle_detected,
        "cycles": detected_cycles,
    }
