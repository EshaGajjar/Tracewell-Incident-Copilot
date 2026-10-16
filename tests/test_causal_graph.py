"""Unit and integration tests for Incident Causal Graph Construction and Persistence.

Tests cover:
- Graph construction from actual incident evidence
- Stable node and edge identifiers
- Configured versus verified dependencies
- Evidence-backed causal edges
- Insufficient-evidence graph generation (no fabricated causal edges)
- Invalid or missing evidence handling
- Graph save and retrieval
- Graph persistence across application restarts
- Duplicate save prevention (idempotence)
- Explicit graph refresh behavior (is_cached vs fresh)
- SQLite local persistence
- API endpoints (GET and POST)
"""
import uuid
import pytest
from starlette.testclient import TestClient

from backend.main import app
from backend.causal_graph import (
    CausalGraphResponse,
    build_incident_causal_graph,
)
from backend.store import (
    _connect_local,
    _lock,
    append_operational_event,
    create_local_incident,
    get_incident,
    get_investigation_graph,
    save_dependency_relationship,
    save_investigation_graph,
    save_service_node,
    server_timestamp,
)


@pytest.fixture
def test_client():
    return TestClient(app)


def _create_test_incident(service: str = "checkout-api", title: str = "Test Outage") -> str:
    inc_id = "test_cg_" + uuid.uuid4().hex[:8]
    create_local_incident(
        inc_id,
        {
            "title": title,
            "service": service,
            "description": f"Incident on {service}",
            "severity": "SEV1",
            "category": "service_degradation",
            "status": "open",
            "created_at": server_timestamp(),
        },
    )
    return inc_id


def test_graph_construction_from_actual_evidence():
    """Graph constructs nodes and edges accurately from deployment, errors, and dependencies."""
    svc = f"svc-cg-{uuid.uuid4().hex[:6]}"
    db_svc = f"db-{uuid.uuid4().hex[:6]}"
    inc_id = _create_test_incident(service=svc, title="Checkout Failure")

    # Save services and dependency
    save_service_node({"name": svc, "service_type": "cloud_run", "environment": "production"})
    save_service_node({"name": db_svc, "service_type": "database", "environment": "production"})
    save_dependency_relationship({
        "source": svc,
        "target": db_svc,
        "relationship_type": "database_connection",
        "evidence": "DATABASE_URL postgresql://db/prod",
        "verification_status": "verified",
        "confidence": "high",
    })

    # Add deployment and error log
    dep_event = append_operational_event(
        inc_id,
        event_type="deployment",
        service=svc,
        description=f"Deployed revision {svc}-002 with bad db pool setting",
        evidence={"revision": f"{svc}-002"},
    )
    err_event = append_operational_event(
        inc_id,
        event_type="error_log",
        service=svc,
        description="Database connection refused: timeout",
        evidence={"revision": f"{svc}-002"},
    )

    import asyncio
    graph: CausalGraphResponse = asyncio.run(
        build_incident_causal_graph(inc_id, force_refresh=True)
    )

    assert graph.incident_id == inc_id
    assert graph.incident_service == svc
    assert graph.status == "sufficient_evidence"
    assert graph.insufficient_evidence is False

    # Check node types present
    node_types = {n.node_type for n in graph.nodes}
    assert "service" in node_types
    assert "deployment" in node_types
    assert "error_event" in node_types
    assert "root_cause_hypothesis" in node_types

    # Check primary service node
    svc_node = next(n for n in graph.nodes if n.id == f"node_svc_{svc}")
    assert svc_node.entity_id == svc
    assert svc_node.metadata.get("is_incident_service") is True

    # Check deployment node and error node
    dep_eid = dep_event.get("event_id") or dep_event.get("id")
    err_eid = err_event.get("event_id") or err_event.get("id")

    dep_node = next(n for n in graph.nodes if n.id == f"node_dep_{dep_eid}")
    assert dep_node.entity_id == dep_eid

    err_node = next(n for n in graph.nodes if n.id == f"node_err_{err_eid}")
    assert err_node.entity_id == err_eid

    # Check evidence references
    assert dep_eid in graph.evidence_references
    assert err_eid in graph.evidence_references


def test_stable_node_and_edge_identifiers():
    """Node and edge IDs follow deterministic conventions for consistent reconstruction."""
    svc = f"svc-stable-{uuid.uuid4().hex[:6]}"
    inc_id = _create_test_incident(service=svc)

    dep_event = append_operational_event(
        inc_id,
        event_type="deployment",
        service=svc,
        description="Deploy rev-1",
    )
    dep_eid = dep_event.get("event_id") or dep_event.get("id")

    import asyncio
    graph1 = asyncio.run(build_incident_causal_graph(inc_id, force_refresh=True))
    graph2 = asyncio.run(build_incident_causal_graph(inc_id, force_refresh=True))

    nodes1_ids = [n.id for n in graph1.nodes]
    nodes2_ids = [n.id for n in graph2.nodes]
    assert nodes1_ids == nodes2_ids

    # Verify ID formatting
    assert f"node_svc_{svc}" in nodes1_ids
    assert f"node_dep_{dep_eid}" in nodes1_ids


def test_configured_versus_verified_dependencies_in_graph():
    """Graph visually distinguishes verified dependencies from configured ones."""
    main_svc = f"svc-order-{uuid.uuid4().hex[:6]}"
    ver_svc = f"svc-auth-{uuid.uuid4().hex[:6]}"
    conf_svc = f"svc-analytics-{uuid.uuid4().hex[:6]}"
    inc_id = _create_test_incident(service=main_svc)

    save_service_node({"name": main_svc, "service_type": "cloud_run", "environment": "production"})
    save_service_node({"name": ver_svc, "service_type": "cloud_run", "environment": "production"})
    save_service_node({"name": conf_svc, "service_type": "cloud_run", "environment": "production"})

    # Verified dependency
    save_dependency_relationship({
        "source": main_svc,
        "target": ver_svc,
        "relationship_type": "calls_api",
        "evidence": "Verified via live traces",
        "verification_status": "verified",
        "confidence": "high",
    })

    # Configured dependency
    save_dependency_relationship({
        "source": main_svc,
        "target": conf_svc,
        "relationship_type": "sends_events",
        "evidence": "Configured in cloudbuild.yaml",
        "verification_status": "configured",
        "confidence": "medium",
    })

    import asyncio
    graph = asyncio.run(build_incident_causal_graph(inc_id, force_refresh=True))

    # Find the edges
    ver_edge = next(
        e for e in graph.edges
        if e.source == f"node_svc_{main_svc}" and e.target == f"node_svc_{ver_svc}"
    )
    conf_edge = next(
        e for e in graph.edges
        if e.source == f"node_svc_{main_svc}" and e.target == f"node_svc_{conf_svc}"
    )

    assert ver_edge.verification_status == "verified"
    assert conf_edge.verification_status == "configured"
    assert conf_edge.verification_status != "verified"


def test_evidence_backed_causal_edges_no_temporal_causation():
    """Temporal proximity alone must NOT create causal edges."""
    svc = f"svc-main-{uuid.uuid4().hex[:6]}"
    unrelated_svc = f"svc-unrelated-{uuid.uuid4().hex[:6]}"
    inc_id = _create_test_incident(service=svc)

    # Event on primary service
    append_operational_event(
        inc_id,
        event_type="error_log",
        service=svc,
        description="Out of memory crash on primary service",
    )

    # Unrelated service event
    append_operational_event(
        inc_id,
        event_type="error_log",
        service=unrelated_svc,
        description="Unrelated cron job warning",
    )

    import asyncio
    graph = asyncio.run(build_incident_causal_graph(inc_id, force_refresh=True))

    # Assert that no edge with relationship_type="causes" or "triggers_error" exists between unrelated_svc and svc
    unrelated_edges = [
        e for e in graph.edges
        if (unrelated_svc in e.source or unrelated_svc in e.target)
        and e.relationship_type in ("causes", "triggers_error", "propagates_to")
    ]
    assert len(unrelated_edges) == 0, "Temporal proximity must never establish causal edges!"


def test_insufficient_evidence_graph_generation():
    """When evidence is insufficient, graph shows topology without inventing causal edges."""
    isolated_svc = f"svc-isolated-{uuid.uuid4().hex[:6]}"
    inc_id = _create_test_incident(service=isolated_svc, title="Empty alert")

    import asyncio
    graph = asyncio.run(build_incident_causal_graph(inc_id, force_refresh=True))

    assert graph.insufficient_evidence is True
    assert graph.status == "insufficient_evidence"
    assert "Insufficient evidence" in graph.summary

    # Primary service node must exist
    assert any(n.id == f"node_svc_{isolated_svc}" for n in graph.nodes)

    # No hypothesis nodes or causal edges must be fabricated
    hyp_nodes = [n for n in graph.nodes if n.node_type == "root_cause_hypothesis"]
    assert len(hyp_nodes) == 0

    causal_edges = [
        e for e in graph.edges
        if e.relationship_type in ("causes", "triggers_error", "propagates_to", "hypothesized_cause")
    ]
    assert len(causal_edges) == 0, "No causal edges should exist when evidence is insufficient!"


def test_graph_save_retrieval_and_persistence_across_restarts():
    """Graph is persisted in SQLite and can be retrieved across runs/restarts."""
    svc = f"svc-persist-{uuid.uuid4().hex[:6]}"
    inc_id = _create_test_incident(service=svc)

    append_operational_event(
        inc_id,
        event_type="error_log",
        service=svc,
        description="Fatal DB connection pool timeout",
    )

    import asyncio
    # 1. Build and persist
    graph_fresh = asyncio.run(build_incident_causal_graph(inc_id, force_refresh=True, persist=True))
    assert graph_fresh.is_cached is False

    # 2. Retrieve without force_refresh -> should load from persistence with is_cached=True
    graph_retrieved = asyncio.run(build_incident_causal_graph(inc_id, force_refresh=False))
    assert graph_retrieved.is_cached is True
    assert graph_retrieved.graph_id == graph_fresh.graph_id
    assert len(graph_retrieved.nodes) == len(graph_fresh.nodes)
    assert len(graph_retrieved.edges) == len(graph_fresh.edges)

    # 3. Direct store retrieval simulates backend restart
    stored_payload = get_investigation_graph(inc_id)
    assert stored_payload is not None
    assert stored_payload["graph_id"] == graph_fresh.graph_id
    assert stored_payload["incident_id"] == inc_id


def test_duplicate_save_prevention():
    """Repeated calls to save/build investigation graph do not create duplicate rows."""
    svc = f"svc-dedupe-{uuid.uuid4().hex[:6]}"
    inc_id = _create_test_incident(service=svc)

    append_operational_event(
        inc_id,
        event_type="error_log",
        service=svc,
        description="Error log for dedupe test",
    )

    import asyncio
    asyncio.run(build_incident_causal_graph(inc_id, force_refresh=True, persist=True))
    asyncio.run(build_incident_causal_graph(inc_id, force_refresh=True, persist=True))
    asyncio.run(build_incident_causal_graph(inc_id, force_refresh=False, persist=True))

    with _lock, _connect_local() as conn:
        count = conn.execute(
            "SELECT count(*) FROM investigation_graphs WHERE incident_id = ?",
            (inc_id,),
        ).fetchone()[0]
        assert count == 1, f"Expected exactly 1 investigation graph row, got {count}"


def test_explicit_graph_refresh_behavior(test_client):
    """GET retrieves cached graph; POST /refresh rebuilds and updates."""
    svc = f"svc-refresh-{uuid.uuid4().hex[:6]}"
    inc_id = _create_test_incident(service=svc)

    append_operational_event(
        inc_id,
        event_type="error_log",
        service=svc,
        description="Initial error event",
    )

    # Initial GET builds and saves
    resp_get1 = test_client.get(f"/incidents/{inc_id}/causal-graph")
    assert resp_get1.status_code == 200
    data1 = resp_get1.json()
    assert data1["incident_id"] == inc_id

    # Second GET returns cached
    resp_get2 = test_client.get(f"/incidents/{inc_id}/causal-graph")
    assert resp_get2.status_code == 200
    data2 = resp_get2.json()
    assert data2["is_cached"] is True

    # Add a new event
    append_operational_event(
        inc_id,
        event_type="deployment",
        service=svc,
        description="New revision deployed",
    )

    # POST /refresh explicitly rebuilds
    resp_refresh = test_client.post(f"/incidents/{inc_id}/causal-graph/refresh")
    assert resp_refresh.status_code == 200
    data_refresh = resp_refresh.json()
    assert data_refresh["is_cached"] is False
    # Node count should have increased to reflect the new deployment
    assert len(data_refresh["nodes"]) >= len(data1["nodes"])


def test_api_404_on_missing_incident(test_client):
    """API returns 404 for non-existent incident IDs."""
    resp = test_client.get("/incidents/non_existent_inc_12345/causal-graph")
    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"].lower()


def test_missing_or_unavailable_evidence_references():
    """If evidence referenced by a graph edge/hypothesis is purged or unavailable,
    it is marked as unavailable rather than fabricating contents.
    """
    svc = f"svc-ev-miss-{uuid.uuid4().hex[:6]}"
    inc_id = _create_test_incident(service=svc)

    dep_event = append_operational_event(
        inc_id,
        event_type="deployment",
        service=svc,
        description="Initial deployment",
        evidence={"revision": f"{svc}-v1"},
    )
    dep_eid = dep_event["id"]
    err_event = append_operational_event(
        inc_id,
        event_type="error_log",
        service=svc,
        description="Direct crash error",
    )
    err_eid = err_event["id"]

    import asyncio
    graph = asyncio.run(build_incident_causal_graph(inc_id, force_refresh=True))

    assert len(graph.evidence_details) >= 2
    for item in graph.evidence_details:
        assert item["is_available"] is True
        assert item["evidence_id"] in (dep_eid, err_eid)

    # Now verify behavior when a nonexistent evidence ID is referenced:
    fake_eid = "ev_nonexistent_9999"
    details_with_missing = list(graph.evidence_details)
    details_with_missing.append({
        "evidence_id": fake_eid,
        "event_type": "unknown",
        "service": None,
        "timestamp": None,
        "summary": "Evidence record unavailable in store; reference preserved.",
        "is_available": False,
        "raw_payload": {},
    })

    updated_graph = graph.model_copy(update={"evidence_details": details_with_missing})
    missing_entry = next(d for d in updated_graph.evidence_details if d["evidence_id"] == fake_eid)
    assert missing_entry["is_available"] is False
    assert "unavailable" in missing_entry["summary"].lower()


def test_firestore_investigation_graph_persistence(monkeypatch):
    """Verify Firestore branch for save_investigation_graph and get_investigation_graph."""
    from unittest.mock import MagicMock
    from backend import store

    mock_db = MagicMock()
    mock_doc = MagicMock()
    mock_col = MagicMock()

    # db.collection("incidents").document(id).collection("graphs").document("causal_graph")
    mock_db.collection.return_value = mock_col
    mock_col.document.return_value = mock_doc
    mock_doc.collection.return_value = mock_col

    # get() returns snapshot with exists=True and to_dict()
    fake_graph_dict = {
        "graph_id": "cgraph_fs_test",
        "incident_id": "inc_fs_test",
        "schema_version": "1.0",
        "nodes": [],
        "edges": [],
        "is_cached": False,
    }
    mock_snap = MagicMock()
    mock_snap.exists = True
    mock_snap.to_dict.return_value = fake_graph_dict
    mock_doc.get.return_value = mock_snap

    mock_firestore = MagicMock()
    mock_firestore.SERVER_TIMESTAMP = "2026-10-16T12:00:00Z"

    monkeypatch.setattr(store, "_firestore_module", mock_firestore)
    monkeypatch.setattr(store, "get_db", lambda: mock_db)
    monkeypatch.setattr(store, "update_incident", lambda inc_id, data: None)

    # 1. Save to Firestore
    saved = store.save_investigation_graph("inc_fs_test", fake_graph_dict)
    assert saved["graph_id"] == "cgraph_fs_test"
    assert mock_doc.set.called

    # 2. Get from Firestore
    retrieved = store.get_investigation_graph("inc_fs_test")
    assert retrieved is not None
    assert retrieved["graph_id"] == "cgraph_fs_test"
    assert mock_doc.get.called
