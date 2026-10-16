"""Tests for service dependency mapping, graph traversal, persistence, and API."""
from __future__ import annotations

import uuid
import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.store import (
    _connect_local,
    get_dependency_relationship,
    get_service_node,
    list_dependency_relationships,
    list_service_nodes,
    save_dependency_relationship,
    save_service_node,
    delete_dependency_relationship,
    delete_service_node,
    create_local_incident,
)
from backend.dependencies import (
    analyze_downstream_impact,
    discover_dependencies_from_evidence,
    ensure_initialized,
    get_service_dependencies,
    init_service_dependencies,
)

client = TestClient(app)


@pytest.fixture(autouse=True)
def setup_dependencies():
    """Ensure baseline dependencies are initialized for each test."""
    ensure_initialized()


# -------------------------------------------------------------------
# 1. Dependency creation and retrieval
# -------------------------------------------------------------------


def test_dependency_creation_and_retrieval():
    """Test saving and retrieving service nodes and dependency relationships."""
    unique_suffix = uuid.uuid4().hex[:8]
    svc_a = f"test-auth-{unique_suffix}"
    svc_b = f"test-db-{unique_suffix}"

    # Save service nodes with required fields
    node_a = save_service_node({
        "name": svc_a,
        "service_type": "cloud_run",
        "environment": "staging",
        "health_status": "healthy",
        "source": "configured",
        "description": "Test auth service",
    })
    assert node_a["name"] == svc_a
    assert node_a["service_type"] == "cloud_run"
    assert node_a["environment"] == "staging"
    assert node_a["health_status"] == "healthy"
    assert node_a["source"] == "configured"

    node_b = save_service_node({
        "name": svc_b,
        "service_type": "database",
        "environment": "staging",
        "health_status": "healthy",
        "source": "configured",
        "description": "Test database",
    })
    assert node_b["name"] == svc_b

    # Save dependency relationship with required fields
    dep = save_dependency_relationship({
        "source": svc_a,
        "target": svc_b,
        "relationship_type": "database_connection",
        "evidence": "Configured DATABASE_URL secret binding",
        "confidence": "high",
        "verification_status": "configured",
        "environment": "staging",
    })
    assert dep["source"] == svc_a
    assert dep["target"] == svc_b
    assert dep["relationship_type"] == "database_connection"
    assert dep["evidence"] == "Configured DATABASE_URL secret binding"
    assert dep["confidence"] == "high"
    assert dep["verification_status"] == "configured"

    # Retrieve service nodes
    retrieved_node = get_service_node(svc_a)
    assert retrieved_node is not None
    assert retrieved_node["name"] == svc_a
    assert retrieved_node["service_type"] == "cloud_run"

    # Retrieve dependency
    retrieved_dep = get_dependency_relationship(svc_a, svc_b, "database_connection")
    assert retrieved_dep is not None
    assert retrieved_dep["source"] == svc_a
    assert retrieved_dep["target"] == svc_b


# -------------------------------------------------------------------
# 2. Dependency traversal (upstream and downstream)
# -------------------------------------------------------------------


def test_dependency_traversal():
    """Test directional upstream and downstream traversal for a service."""
    unique_suffix = uuid.uuid4().hex[:8]
    client_svc = f"client-svc-{unique_suffix}"
    core_svc = f"core-svc-{unique_suffix}"
    db_svc = f"db-svc-{unique_suffix}"

    # client_svc -> core_svc -> db_svc
    save_dependency_relationship({
        "source": client_svc,
        "target": core_svc,
        "relationship_type": "sync_http",
        "evidence": "Client calls Core REST API",
        "confidence": "high",
        "verification_status": "configured",
    })
    save_dependency_relationship({
        "source": core_svc,
        "target": db_svc,
        "relationship_type": "database_connection",
        "evidence": "Core connects to Postgres",
        "confidence": "high",
        "verification_status": "configured",
    })

    # For core_svc:
    # Upstream dependency (services core_svc relies on): db_svc
    # Downstream dependent (services calling core_svc): client_svc
    core_deps = get_service_dependencies(core_svc, direction="both")
    upstream_targets = [d["target"] for d in core_deps["upstream"]]
    downstream_callers = [d["source"] for d in core_deps["downstream"]]

    assert db_svc in upstream_targets
    assert client_svc in downstream_callers

    # Test upstream-only direction
    upstream_only = get_service_dependencies(core_svc, direction="upstream")
    assert len(upstream_only["upstream"]) >= 1
    assert len(upstream_only["downstream"]) == 0

    # Test downstream-only direction
    downstream_only = get_service_dependencies(core_svc, direction="downstream")
    assert len(downstream_only["downstream"]) >= 1
    assert len(downstream_only["upstream"]) == 0


# -------------------------------------------------------------------
# 3. Downstream impact identification
# -------------------------------------------------------------------


def test_downstream_impact_identification():
    """Given an affected service, identify potentially and confirmed impacted services."""
    unique_suffix = uuid.uuid4().hex[:8]
    leaf_db = f"leaf-db-{unique_suffix}"
    mid_api = f"mid-api-{unique_suffix}"
    top_gateway = f"top-gateway-{unique_suffix}"

    # leaf_db is called by mid_api, which is called by top_gateway
    save_dependency_relationship({
        "source": mid_api,
        "target": leaf_db,
        "relationship_type": "database_connection",
        "evidence": "DATABASE_URL postgresql connection",
        "confidence": "high",
        "verification_status": "verified",
    })
    save_dependency_relationship({
        "source": top_gateway,
        "target": mid_api,
        "relationship_type": "sync_http",
        "evidence": "Gateway reverse proxy routing",
        "confidence": "high",
        "verification_status": "configured",
    })

    # When leaf_db is degraded/affected:
    # mid_api is direct downstream (depth 1, verified)
    # top_gateway is transitive downstream (depth 2)
    impact = analyze_downstream_impact(leaf_db)
    impacted = {item["service_name"]: item for item in impact["impacted_services"]}

    assert mid_api in impacted
    assert top_gateway in impacted

    # mid_api has depth 1 and confirmed impact (direct verified dependency)
    assert impacted[mid_api]["depth"] == 1
    assert impacted[mid_api]["impact_level"] == "confirmed"

    # top_gateway has depth 2 and potential impact (transitive)
    assert impacted[top_gateway]["depth"] == 2
    assert impacted[top_gateway]["impact_level"] == "potential"

    # Counts
    assert impact["total_impacted"] == 2
    assert impact["confirmed_impact_count"] >= 1
    assert impact["potential_impact_count"] >= 1
    assert impact["cycle_detected"] is False


def test_downstream_impact_with_active_incident():
    """A downstream service with an active incident is marked as confirmed impact."""
    unique_suffix = uuid.uuid4().hex[:8]
    svc_target = f"provider-{unique_suffix}"
    svc_caller = f"consumer-{unique_suffix}"

    save_dependency_relationship({
        "source": svc_caller,
        "target": svc_target,
        "relationship_type": "sync_http",
        "evidence": "Synchronous RPC",
        "confidence": "high",
        "verification_status": "configured",
    })

    # Create an active incident for the consumer
    inc_id = f"inc_{unique_suffix}"
    create_local_incident(inc_id, {
        "title": "Consumer errors spiking",
        "service": svc_caller,
        "description": "503 service unavailable",
        "status": "open",
    })

    impact = analyze_downstream_impact(svc_target)
    impacted = {item["service_name"]: item for item in impact["impacted_services"]}

    assert svc_caller in impacted
    assert impacted[svc_caller]["impact_level"] == "confirmed"
    assert any("active incident" in r.lower() for r in impacted[svc_caller]["reasons"])


# -------------------------------------------------------------------
# 4. Cycles in the dependency graph
# -------------------------------------------------------------------


def test_cycles_in_dependency_graph():
    """Verify cycle detection prevents infinite loops and reports cycle paths."""
    unique_suffix = uuid.uuid4().hex[:8]
    svc_x = f"cycle-x-{unique_suffix}"
    svc_y = f"cycle-y-{unique_suffix}"
    svc_z = f"cycle-z-{unique_suffix}"

    # Cycle: svc_x -> svc_y -> svc_z -> svc_x
    save_dependency_relationship({
        "source": svc_x,
        "target": svc_y,
        "relationship_type": "sync_http",
        "evidence": "X calls Y",
    })
    save_dependency_relationship({
        "source": svc_y,
        "target": svc_z,
        "relationship_type": "sync_http",
        "evidence": "Y calls Z",
    })
    save_dependency_relationship({
        "source": svc_z,
        "target": svc_x,
        "relationship_type": "sync_http",
        "evidence": "Z calls X (cycle)",
    })

    # Traversal must finish cleanly without infinite recursion
    impact = analyze_downstream_impact(svc_y, max_depth=10)
    assert impact["cycle_detected"] is True
    assert len(impact["cycles"]) > 0

    # Ensure all services in the cycle were evaluated without crashing
    impacted_names = [item["service_name"] for item in impact["impacted_services"]]
    assert svc_x in impacted_names


# -------------------------------------------------------------------
# 5. Missing dependency information
# -------------------------------------------------------------------


def test_missing_dependency_information():
    """Verify clean behavior when dependencies or services do not exist."""
    unique_suffix = uuid.uuid4().hex[:8]
    isolated_svc = f"isolated-{unique_suffix}"

    save_service_node({
        "name": isolated_svc,
        "service_type": "microservice",
        "environment": "production",
        "source": "configured",
    })

    # Direct dependencies query
    deps = get_service_dependencies(isolated_svc)
    assert deps["upstream"] == []
    assert deps["downstream"] == []
    assert deps["upstream_count"] == 0
    assert deps["downstream_count"] == 0

    # Downstream impact query
    impact = analyze_downstream_impact(isolated_svc)
    assert impact["impacted_services"] == []
    assert impact["total_impacted"] == 0
    assert impact["confirmed_impact_count"] == 0
    assert impact["potential_impact_count"] == 0
    assert impact["cycle_detected"] is False

    # API 404 for completely unknown service
    r = client.get(f"/services/non-existent-{unique_suffix}")
    assert r.status_code == 404

    r = client.get(f"/services/non-existent-{unique_suffix}/dependencies")
    assert r.status_code == 404

    r = client.get(f"/services/non-existent-{unique_suffix}/downstream-impact")
    assert r.status_code == 404


# -------------------------------------------------------------------
# 6. Duplicate relationship prevention
# -------------------------------------------------------------------


def test_duplicate_relationship_prevention():
    """Verify inserting identical relationship updates rather than duplicating."""
    unique_suffix = uuid.uuid4().hex[:8]
    src = f"dedupe-src-{unique_suffix}"
    tgt = f"dedupe-tgt-{unique_suffix}"

    # First insert
    save_dependency_relationship({
        "source": src,
        "target": tgt,
        "relationship_type": "sync_http",
        "evidence": "Initial configuration evidence",
        "confidence": "medium",
        "verification_status": "configured",
    })

    # Second insert with updated evidence and status
    save_dependency_relationship({
        "source": src,
        "target": tgt,
        "relationship_type": "sync_http",
        "evidence": "Updated verified evidence from Cloud Logging",
        "confidence": "high",
        "verification_status": "verified",
    })

    # Query relations between these two services
    rels = list_dependency_relationships(source=src, target=tgt)
    assert len(rels) == 1
    assert rels[0]["evidence"] == "Updated verified evidence from Cloud Logging"
    assert rels[0]["verification_status"] == "verified"
    assert rels[0]["confidence"] == "high"


# -------------------------------------------------------------------
# 7. API behavior
# -------------------------------------------------------------------


def test_api_get_services_and_dependencies():
    """Test GET /services and GET /dependencies endpoints."""
    r_services = client.get("/services")
    assert r_services.status_code == 200
    services = r_services.json()
    assert isinstance(services, list)
    assert len(services) >= 4  # Canonical services present
    service_names = [s["name"] for s in services]
    assert "payment-api" in service_names
    assert "order-service" in service_names

    # Check required fields on each service node
    for s in services:
        assert "name" in s
        assert "service_type" in s
        assert "environment" in s
        assert "source" in s

    r_deps = client.get("/dependencies")
    assert r_deps.status_code == 200
    body = r_deps.json()
    assert "services" in body
    assert "dependencies" in body
    assert body["total_services"] == len(body["services"])
    assert body["total_dependencies"] == len(body["dependencies"])

    # Check required fields on dependencies
    for d in body["dependencies"]:
        assert "source" in d
        assert "target" in d
        assert "relationship_type" in d
        assert "evidence" in d
        assert "confidence" in d
        assert "verification_status" in d


def test_api_create_dependency():
    """Test POST /dependencies endpoint creates relationship and auto-creates nodes."""
    unique_suffix = uuid.uuid4().hex[:8]
    src = f"api-src-{unique_suffix}"
    tgt = f"api-tgt-{unique_suffix}"

    payload = {
        "source": src,
        "target": tgt,
        "relationship_type": "sync_http",
        "evidence": "Configured API Gateway route",
        "confidence": "high",
        "verification_status": "configured",
        "environment": "production",
    }
    r = client.post("/dependencies", json=payload)
    assert r.status_code == 201
    data = r.json()
    assert data["source"] == src
    assert data["target"] == tgt
    assert data["relationship_type"] == "sync_http"

    # Verify nodes were registered
    assert client.get(f"/services/{src}").status_code == 200
    assert client.get(f"/services/{tgt}").status_code == 200

    # Validation failure: missing source
    r_bad = client.post("/dependencies", json={"source": "", "target": tgt})
    assert r_bad.status_code == 422


def test_api_service_dependencies_and_downstream_impact():
    """Test GET /services/{name}/dependencies and GET /services/{name}/downstream-impact."""
    # payment-api is called by order-service, and calls payment-db, payment-cache, auth-service
    r_deps = client.get("/services/payment-api/dependencies")
    assert r_deps.status_code == 200
    deps = r_deps.json()
    assert deps["service"] == "payment-api"
    upstream_targets = [d["target"] for d in deps["upstream"]]
    downstream_sources = [d["source"] for d in deps["downstream"]]

    assert "payment-db" in upstream_targets
    assert "order-service" in downstream_sources

    # Downstream impact of payment-api
    r_impact = client.get("/services/payment-api/downstream-impact")
    assert r_impact.status_code == 200
    impact = r_impact.json()
    assert impact["service"] == "payment-api"
    assert impact["total_impacted"] >= 1
    impacted_names = [i["service_name"] for i in impact["impacted_services"]]
    assert "order-service" in impacted_names


# -------------------------------------------------------------------
# 8. Local SQLite fallback
# -------------------------------------------------------------------


def test_local_sqlite_fallback_and_schema():
    """Verify SQLite persistence mode correctly maintains table schemas and constraints."""
    with _connect_local() as conn:
        tables = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "services" in tables
        assert "service_dependencies" in tables

        # Verify columns in services table
        service_cols = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(services)").fetchall()
        }
        assert {"name", "service_type", "environment", "health_status", "source", "payload"}.issubset(service_cols)

        # Verify columns in service_dependencies table
        dep_cols = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(service_dependencies)").fetchall()
        }
        assert {"source_service", "target_service", "relationship_type", "evidence", "confidence", "verification_status"}.issubset(dep_cols)

        # Verify unique index exists
        indices = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            ).fetchall()
        }
        assert "idx_service_dependencies_unique" in indices


# -------------------------------------------------------------------
# 9. Discovery from explicit evidence
# -------------------------------------------------------------------


def test_discovery_from_operational_evidence():
    """Verify evidence upgrades relationship verification status strictly with proof."""
    # Simulate an error log for payment-api referencing DATABASE_URL
    event = {
        "service": "payment-api",
        "description": "500 Internal Server Error: DATABASE_URL invalid on connection pool init",
        "timestamp": "2026-10-16T08:00:00Z",
        "evidence": {
            "log": {
                "payload": "DATABASE_URL invalid: could not parse PostgreSQL connection string"
            }
        },
    }

    result = discover_dependencies_from_evidence(operational_events=[event])
    assert result["status"] == "success"
    assert result["updated_relationships"] >= 1

    dep = get_dependency_relationship("payment-api", "payment-db", "database_connection")
    assert dep is not None
    assert dep["verification_status"] == "verified"
    assert "DATABASE_URL invalid" in dep["evidence"]
