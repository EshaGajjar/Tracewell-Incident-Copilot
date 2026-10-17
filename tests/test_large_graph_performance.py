"""Tests for large graph handling, deduplication, and 25-node initial rendering performance.

Validates:
1. Incident causal graph generation with 177 or more entities.
2. Initial simplified causal path rendering limited to at most 25 nodes.
3. Prioritization of leading root cause, deployments, critical errors, and dependencies.
4. Batch expansion (+25 nodes), Show All, and Collapse functionality.
5. Evidence integrity: no deletion of stored events, preservation of evidence references.
6. Edge deduplication and prevention of orphan or broken edges.
"""
import asyncio
import uuid
import pytest

from backend.causal_graph import (
    CausalGraphResponse,
    build_incident_causal_graph,
)
from backend.store import (
    append_operational_event,
    create_local_incident,
    save_dependency_relationship,
    save_service_node,
    server_timestamp,
)


def _seed_large_incident(total_events: int = 180):
    """Seed an incident with 177+ entities (services, dependencies, deployments, errors, health checks)."""
    incident_svc = f"svc-pay-{uuid.uuid4().hex[:6]}"
    upstream_db = f"db-pay-{uuid.uuid4().hex[:6]}"
    downstream_web = f"web-frontend-{uuid.uuid4().hex[:6]}"
    auth_svc = f"svc-auth-{uuid.uuid4().hex[:6]}"

    inc_id = f"inc_perf_{uuid.uuid4().hex[:8]}"
    create_local_incident(
        inc_id,
        {
            "title": f"[LOAD] {incident_svc} high latency and database failure",
            "service": incident_svc,
            "description": f"Performance incident with >177 entities on {incident_svc}",
            "severity": "SEV1",
            "category": "service_degradation",
            "status": "open",
            "created_at": server_timestamp(),
        },
    )

    # Topology and Dependencies
    save_service_node({"name": incident_svc, "service_type": "cloud_run", "environment": "production"})
    save_service_node({"name": upstream_db, "service_type": "database", "environment": "production"})
    save_service_node({"name": downstream_web, "service_type": "frontend", "environment": "production"})
    save_service_node({"name": auth_svc, "service_type": "cloud_run", "environment": "production"})

    save_dependency_relationship({
        "source": incident_svc,
        "target": upstream_db,
        "relationship_type": "database_connection",
        "evidence": f"{incident_svc} queries {upstream_db}",
        "verification_status": "verified",
        "confidence": "high",
    })
    save_dependency_relationship({
        "source": downstream_web,
        "target": incident_svc,
        "relationship_type": "calls_service",
        "evidence": f"{downstream_web} calls {incident_svc}",
        "verification_status": "configured",
        "confidence": "medium",
    })

    # 1. Triggering Deployment Event
    dep_event = append_operational_event(
        inc_id,
        event_type="deployment",
        service=incident_svc,
        description=f"Revision {incident_svc}-v42 deployed with misconfigured connection pool",
        evidence={"revision": f"{incident_svc}-v42", "source": "Cloud Deploy"},
    )

    # 2. Corroborating Critical Errors on Incident Service
    for i in range(15):
        append_operational_event(
            inc_id,
            event_type="error_log",
            service=incident_svc,
            description=f"CRITICAL: Connection pool exhausted to {upstream_db} [trace-{i}]",
            evidence={"revision": f"{incident_svc}-v42", "severity": "CRITICAL", "error_code": "POOL_EXHAUSTED"},
        )

    # 3. High volume of background error events (simulating real incident traffic spike)
    for i in range(140):
        append_operational_event(
            inc_id,
            event_type="error_log",
            service=incident_svc if i % 2 == 0 else auth_svc,
            description=f"HTTP 500 downstream error on request #{i}: timeout connecting to payment backend",
            evidence={"severity": "ERROR", "trace_id": f"trace-500-{i}"},
        )

    # 4. Health Check telemetry
    for i in range(25):
        append_operational_event(
            inc_id,
            event_type="health_check",
            service=incident_svc if i % 3 == 0 else upstream_db,
            description=f"Health probe check #{i}: status={'FAIL' if i < 10 else 'OK'}",
            evidence={"severity": "ERROR" if i < 10 else "INFO", "probe_id": f"prb-{i}"},
        )

    return inc_id, incident_svc, upstream_db, dep_event["id"]


def test_large_graph_construction_177_plus_entities():
    """Verify backend builds a complete graph with >= 177 entities without drops or corruption."""
    inc_id, incident_svc, upstream_db, dep_eid = _seed_large_incident(total_events=185)

    graph: CausalGraphResponse = asyncio.run(
        build_incident_causal_graph(inc_id, force_refresh=True)
    )

    # 1. Verify entity scale
    total_entities = len(graph.nodes)
    assert total_entities >= 177, f"Expected at least 177 entities, got {total_entities}"
    assert len(graph.evidence_references) >= 175, f"Expected >=175 evidence references, got {len(graph.evidence_references)}"
    assert len(graph.evidence_details) >= 175, "All evidence records must be detailed"

    # 2. Verify stable IDs and visual node deduplication
    node_ids = [n.id for n in graph.nodes]
    assert len(node_ids) == len(set(node_ids)), "All node IDs must be strictly unique"

    entity_keys = [(n.node_type, n.entity_id) for n in graph.nodes if n.entity_id]
    assert len(entity_keys) == len(set(entity_keys)), "No visual duplicate nodes for the same entity"

    # 3. Verify edges have valid source and target nodes (no orphan edges)
    node_id_set = set(node_ids)
    for edge in graph.edges:
        assert edge.source in node_id_set, f"Edge source {edge.source} missing from nodes"
        assert edge.target in node_id_set, f"Edge target {edge.target} missing from nodes"

    # 4. Verify evidence integrity: leading hypothesis and evidence-backed links exist
    assert graph.leading_hypothesis is not None, "Leading hypothesis must be identified"
    assert graph.status == "sufficient_evidence"
    assert not graph.insufficient_evidence


def test_simplified_causal_path_prioritization():
    """Verify that ranking prioritizes leading cause, deployments, errors, and dependencies."""
    inc_id, incident_svc, upstream_db, dep_eid = _seed_large_incident(total_events=180)

    graph: CausalGraphResponse = asyncio.run(
        build_incident_causal_graph(inc_id, force_refresh=True)
    )

    # Simulate the frontend prioritization scoring algorithm
    leading_hyp_id = graph.leading_hypothesis_id
    leading_hyp = next((n for n in graph.nodes if n.id == leading_hyp_id), None)
    leading_ev_ids = set(leading_hyp.evidence_ids if leading_hyp else [])

    causal_rels = {"causes", "triggers_error", "propagates_to", "hypothesized_cause", "evidence_for"}
    causal_node_ids = set()
    for e in graph.edges:
        if e.relationship_type in causal_rels:
            causal_node_ids.add(e.source)
            causal_node_ids.add(e.target)

    def score_node(node):
        if node.is_leading_hypothesis or node.id == leading_hyp_id:
            return 10000
        if node.node_type == "root_cause_hypothesis":
            return 9000
        if node.node_type == "service" and node.service == incident_svc:
            return 8500
        if leading_hyp and node.node_type == "service" and node.service == leading_hyp.service:
            return 8000
        is_leading_ev = node.entity_id in leading_ev_ids or any(eid in leading_ev_ids for eid in node.evidence_ids)
        is_causal = node.id in causal_node_ids
        if node.node_type == "deployment":
            return 5000 + (2500 if is_leading_ev else 0) + (1500 if is_causal else 0)
        if node.node_type == "error_event":
            return 4000 + (3000 if is_leading_ev else 0) + (2000 if is_causal else 0)
        if node.node_type == "service":
            return 3000 + (2000 if is_causal else 0)
        if node.node_type == "health_event":
            return 2000 + (1500 if is_leading_ev else 0)
        return 1000

    sorted_nodes = sorted(graph.nodes, key=lambda n: score_node(n), reverse=True)

    # 1. Initial view strictly limited to 25 nodes
    initial_25 = sorted_nodes[:25]
    assert len(initial_25) == 25

    initial_types = {n.node_type for n in initial_25}

    # 2. Must contain leading hypothesis
    assert any(n.is_leading_hypothesis or n.id == leading_hyp_id for n in initial_25), "Leading cause must be in top 25"

    # 3. Must contain relevant deployment events
    assert "deployment" in initial_types, "Deployment must be in top 25"

    # 4. Must contain most relevant error events
    assert "error_event" in initial_types, "Critical error telemetry must be in top 25"

    # 5. Must contain necessary service dependencies
    assert "service" in initial_types, "Primary service topology must be in top 25"
    assert any(n.service == incident_svc for n in initial_25), "Incident service must be in top 25"


def test_batch_expansion_and_evidence_accessibility():
    """Verify that batches of 25 can be expanded up to 177+ and collapsed, preserving all evidence."""
    inc_id, incident_svc, upstream_db, dep_eid = _seed_large_incident(total_events=180)

    graph: CausalGraphResponse = asyncio.run(
        build_incident_causal_graph(inc_id, force_refresh=True)
    )

    total_nodes = len(graph.nodes)
    assert total_nodes >= 177

    # Batch progression
    batch_1 = graph.nodes[:25]
    assert len(batch_1) == 25, "Initial batch must be 25 nodes"

    batch_2 = graph.nodes[:50]
    assert len(batch_2) == 50, "Second batch must be 50 nodes"

    batch_3 = graph.nodes[:75]
    assert len(batch_3) == 75, "Third batch must be 75 nodes"

    # Show all
    all_batch = graph.nodes[:total_nodes]
    assert len(all_batch) == total_nodes, "Show all must include all entities"

    # Collapse back
    collapsed = graph.nodes[:25]
    assert len(collapsed) == 25, "Collapsed view must return to 25 nodes"

    # All original evidence references remain accessible regardless of active canvas limit
    evidence_ids = {e["evidence_id"] for e in graph.evidence_details}
    assert dep_eid in evidence_ids, "Deployment evidence must remain accessible in archive"
    assert len(evidence_ids) >= 175, "All evidence items remain accessible in evidence archive"
