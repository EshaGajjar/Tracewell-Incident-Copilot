"""Tests for evidence-based event correlation engine and APIs."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.store import (
    _connect_local,
    append_operational_event,
    create_local_incident,
    get_incident,
    list_correlated_events,
    save_correlated_event,
    save_dependency_relationship,
    save_service_node,
)
from backend.dependencies import ensure_initialized
from backend.correlation import (
    CorrelationClassification,
    correlate_events_for_incident,
    classify_event_for_incident,
)

client = TestClient(app)


@pytest.fixture(autouse=True)
def setup_baseline():
    ensure_initialized()


# -------------------------------------------------------------------
# 1. Exact service matching
# -------------------------------------------------------------------


def test_exact_service_matching():
    """Events originating from the exact incident service are classified as direct_match."""
    unique = uuid.uuid4().hex[:8]
    inc_id = f"inc_{unique}"
    service = "payment-api"

    create_local_incident(inc_id, {
        "title": "Payment gateway timeout",
        "service": service,
        "description": "504 Gateway Timeout during checkout",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "open",
    })

    event = {
        "id": f"ev_{unique}",
        "incident_id": inc_id,
        "service": service,
        "event_type": "error_log",
        "description": "Database connection timeout after 5000ms",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "evidence": {
            "source": "Cloud Logging",
            "severity": "ERROR",
            "log": {"payload": "TimeoutError: connection pool exhausted"},
        },
    }

    res = correlate_events_for_incident(inc_id, candidate_events=[event])
    assert res.total_events >= 1

    matched = next((e for e in res.events if e.event_id == event["id"]), None)
    assert matched is not None
    assert matched.classification == CorrelationClassification.DIRECT_MATCH
    assert matched.classification_label == "Direct match"
    assert "Exact service match" in matched.correlation_reason
    assert matched.relationship_direction == "self"
    assert matched.is_causal_candidate is True


# -------------------------------------------------------------------
# 2. Revision matching
# -------------------------------------------------------------------


def test_revision_matching():
    """Events matching target revision identified in incident context are marked as direct revision matches."""
    unique = uuid.uuid4().hex[:8]
    inc_id = f"inc_{unique}"
    service = "payment-api"
    faulty_revision = f"payment-api-00042-{unique}"

    create_local_incident(inc_id, {
        "title": "Deployment crash",
        "service": service,
        "description": f"Crash loop on revision {faulty_revision}",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "open",
        "pending_action": {
            "target_revision": faulty_revision,
            "action": "rollback",
        },
    })

    event = {
        "id": f"rev_ev_{unique}",
        "incident_id": inc_id,
        "service": service,
        "event_type": "deployment",
        "description": f"Cloud Run revision {faulty_revision} rolled out 100% traffic",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "evidence": {
            "revision": {"name": faulty_revision},
            "source": "Cloud Run API",
        },
    }

    res = correlate_events_for_incident(inc_id, candidate_events=[event])
    matched = next((e for e in res.events if e.event_id == event["id"]), None)
    assert matched is not None
    assert matched.classification == CorrelationClassification.DIRECT_MATCH
    assert "Exact revision match" in matched.correlation_reason
    assert matched.revision == faulty_revision
    assert matched.is_causal_candidate is True


# -------------------------------------------------------------------
# 3. Dependency-based correlation (Upstream and Downstream)
# -------------------------------------------------------------------


def test_upstream_dependency_correlation():
    """An event on an upstream dependency is classified as dependency_related and potential root cause."""
    unique = uuid.uuid4().hex[:8]
    inc_id = f"inc_{unique}"
    incident_service = "payment-api"
    upstream_service = "payment-db"

    create_local_incident(inc_id, {
        "title": "Payment failures",
        "service": incident_service,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "open",
    })

    # payment-db event
    event = {
        "id": f"db_ev_{unique}",
        "incident_id": inc_id,
        "service": upstream_service,
        "event_type": "error_log",
        "description": "PostgreSQL connection limit reached (max_connections=100)",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "evidence": {"source": "Cloud SQL"},
    }

    res = correlate_events_for_incident(inc_id, candidate_events=[event])
    matched = next((e for e in res.events if e.event_id == event["id"]), None)
    assert matched is not None
    assert matched.classification == CorrelationClassification.DEPENDENCY_RELATED
    assert matched.classification_label == "Dependency-related"
    assert matched.relationship_direction == "upstream_dependency"
    assert "Upstream dependency" in matched.correlation_reason
    assert matched.is_causal_candidate is True  # Upstream errors can be root causes


def test_downstream_dependent_correlation():
    """An event on a downstream caller is classified as dependency_related cascading impact."""
    unique = uuid.uuid4().hex[:8]
    inc_id = f"inc_{unique}"
    incident_service = "payment-api"
    downstream_service = "order-service"  # order-service calls payment-api

    create_local_incident(inc_id, {
        "title": "Payment failures",
        "service": incident_service,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "open",
    })

    event = {
        "id": f"order_ev_{unique}",
        "incident_id": inc_id,
        "service": downstream_service,
        "event_type": "error_log",
        "description": "Checkout failed: payment-api returned 500 Internal Server Error",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "evidence": {"source": "order-service logs"},
    }

    res = correlate_events_for_incident(inc_id, candidate_events=[event])
    matched = next((e for e in res.events if e.event_id == event["id"]), None)
    assert matched is not None
    assert matched.classification == CorrelationClassification.DEPENDENCY_RELATED
    assert matched.relationship_direction == "downstream_dependent"
    assert "Downstream dependent" in matched.correlation_reason
    assert matched.is_causal_candidate is False  # Consumer failure is symptom, not cause


# -------------------------------------------------------------------
# 4. Timestamp-only association with non-causation safeguard
# -------------------------------------------------------------------


def test_timestamp_only_association_does_not_assert_causation():
    """Events occurring in the window from unrelated services must NOT be claimed as causal."""
    unique = uuid.uuid4().hex[:8]
    inc_id = f"inc_{unique}"
    incident_service = "payment-api"
    unrelated_service = f"inventory-sync-{unique}"  # Not in dependency graph

    now = datetime.now(timezone.utc)
    create_local_incident(inc_id, {
        "title": "Payment processing halted",
        "service": incident_service,
        "created_at": now.isoformat(),
        "status": "open",
    })

    # Event happens at the exact same minute
    event = {
        "id": f"coincidental_ev_{unique}",
        "incident_id": inc_id,
        "service": unrelated_service,
        "event_type": "deployment",
        "description": "Routine catalog sync batch job completed",
        "timestamp": (now - timedelta(minutes=2)).isoformat(),
        "evidence": {"source": "Batch Job"},
    }

    res = correlate_events_for_incident(inc_id, candidate_events=[event], lookback_minutes=60)
    matched = next((e for e in res.events if e.event_id == event["id"]), None)
    assert matched is not None
    assert matched.classification == CorrelationClassification.TEMPORALLY_ASSOCIATED
    assert matched.classification_label == "Temporally associated"
    # Strict rule check:
    assert "Timestamp proximity alone does not establish causation" in matched.correlation_reason
    assert matched.is_causal_candidate is False
    assert matched.confidence == "low"


# -------------------------------------------------------------------
# 5. Unrelated services outside window
# -------------------------------------------------------------------


def test_unrelated_services_outside_window():
    """Events from non-dependent services outside the window are classified as unrelated."""
    unique = uuid.uuid4().hex[:8]
    inc_id = f"inc_{unique}"
    incident_service = "payment-api"
    now = datetime.now(timezone.utc)

    create_local_incident(inc_id, {
        "title": "Payment API incident",
        "service": incident_service,
        "created_at": now.isoformat(),
        "status": "open",
    })

    # Event happened 3 days ago on an independent service
    event = {
        "id": f"old_ev_{unique}",
        "incident_id": inc_id,
        "service": "analytics-export",
        "event_type": "cron_job",
        "description": "Weekly data dump",
        "timestamp": (now - timedelta(days=3)).isoformat(),
    }

    res = correlate_events_for_incident(inc_id, candidate_events=[event], lookback_minutes=30)
    matched = next((e for e in res.events if e.event_id == event["id"]), None)
    assert matched is not None
    assert matched.classification == CorrelationClassification.UNRELATED
    assert matched.classification_label == "Unrelated"
    assert "Unrelated" in matched.correlation_reason


# -------------------------------------------------------------------
# 6. Missing evidence handling
# -------------------------------------------------------------------


def test_missing_evidence_handling():
    """Correlation preserves missing evidence gracefully without inventing facts."""
    unique = uuid.uuid4().hex[:8]
    inc_id = f"inc_{unique}"

    create_local_incident(inc_id, {
        "title": "Incident with minimal evidence",
        "service": "payment-api",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "open",
    })

    sparse_event = {
        "id": f"sparse_{unique}",
        "service": "payment-api",
        # No evidence dict, no revision, no severity
    }

    res = correlate_events_for_incident(inc_id, candidate_events=[sparse_event])
    matched = next((e for e in res.events if e.event_id == sparse_event["id"]), None)
    assert matched is not None
    assert matched.classification == CorrelationClassification.DIRECT_MATCH
    assert matched.revision is None
    assert matched.evidence == {}
    assert matched.severity == "INFO"


# -------------------------------------------------------------------
# 7. Duplicate handling
# -------------------------------------------------------------------


def test_duplicate_correlation_prevention():
    """Re-correlating events updates existing records rather than duplicating them."""
    unique = uuid.uuid4().hex[:8]
    inc_id = f"inc_{unique}"
    service = "payment-api"

    create_local_incident(inc_id, {
        "title": "Duplicate test incident",
        "service": service,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "open",
    })

    event = {
        "id": f"dedupe_ev_{unique}",
        "incident_id": inc_id,
        "service": service,
        "event_type": "error_log",
        "description": "First version of error log",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    # First correlation
    correlate_events_for_incident(inc_id, candidate_events=[event], persist=True)

    # Second correlation with same event ID
    event["description"] = "Second updated version of error log"
    correlate_events_for_incident(inc_id, candidate_events=[event], persist=True)

    # Check store directly
    stored = list_correlated_events(inc_id)
    matching = [e for e in stored if e.get("event_id") == event["id"]]
    assert len(matching) == 1
    assert matching[0]["description"] == "Second updated version of error log"


# -------------------------------------------------------------------
# 8. API behavior
# -------------------------------------------------------------------


def test_correlated_events_api_endpoint():
    """Test GET /incidents/{incident_id}/correlated-events and classification filter."""
    unique = uuid.uuid4().hex[:8]
    inc_id = f"inc_{unique}"
    service = "payment-api"

    create_local_incident(inc_id, {
        "title": "API Correlation Test",
        "service": service,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "open",
    })

    # Record operational event for the incident
    append_operational_event(
        inc_id,
        event_type="error_log",
        service=service,
        description="500 Internal Server Error in /pay",
    )

    # Query endpoint
    r = client.get(f"/incidents/{inc_id}/correlated-events")
    assert r.status_code == 200
    data = r.json()
    assert data["incident_id"] == inc_id
    assert data["incident_service"] == service
    assert data["total_events"] >= 1
    assert data["direct_matches_count"] >= 1

    # Filter by classification
    r_filtered = client.get(f"/incidents/{inc_id}/correlated-events?classification=direct_match")
    assert r_filtered.status_code == 200
    assert all(e["classification"] == "direct_match" for e in r_filtered.json()["events"])

    # Test backward-compatible correlated=true on /events
    r_events_corr = client.get(f"/incidents/{inc_id}/events?correlated=true")
    assert r_events_corr.status_code == 200
    assert isinstance(r_events_corr.json(), list)

    # 404 for unknown incident
    r_404 = client.get(f"/incidents/unknown_nonexistent_{unique}/correlated-events")
    assert r_404.status_code == 404


# -------------------------------------------------------------------
# 9. Local SQLite fallback
# -------------------------------------------------------------------


def test_sqlite_fallback_correlated_events_table():
    """Verify local SQLite properly creates and indexes correlated_events."""
    with _connect_local() as conn:
        tables = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "correlated_events" in tables

        cols = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(correlated_events)").fetchall()
        }
        assert {"incident_id", "event_id", "classification", "correlation_reason", "payload", "created_at"}.issubset(cols)
