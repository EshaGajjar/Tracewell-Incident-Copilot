"""Unit and integration tests for Evidence-Based Root-Cause Analysis.

Tests cover:
- Strong supporting evidence
- Conflicting evidence
- Insufficient evidence
- Invalid or fabricated evidence IDs
- Configured versus verified dependencies
- AI unavailable / local fallback
- API responses (GET and POST)
- Persistence and duplicate prevention
"""
import uuid
import pytest
from starlette.testclient import TestClient

from backend.main import app
from backend.root_cause import (
    RootCauseAnalysisResponse,
    build_evidence_catalog,
    perform_root_cause_analysis,
    validate_and_sanitize_evidence_ids,
)
from backend.store import (
    _connect_local,
    _lock,
    append_operational_event,
    create_local_incident,
    get_incident,
    get_root_cause_analysis,
    save_dependency_relationship,
    save_service_node,
    server_timestamp,
)


@pytest.fixture
def test_client():
    return TestClient(app)


def _create_test_incident(service: str = "payment-api", title: str = "Test Outage") -> str:
    inc_id = "test_rc_" + uuid.uuid4().hex[:8]
    create_local_incident(
        inc_id,
        {
            "title": title,
            "service": service,
            "description": f"Spike in errors reported for {service}",
            "severity": "SEV1",
            "category": "service_degradation",
            "status": "open",
            "created_at": server_timestamp(),
        },
    )
    return inc_id


def test_strong_supporting_evidence_direct_match():
    """Direct deployment of a misconfigured revision + matching direct error logs produces a strong hypothesis."""
    inc_id = _create_test_incident(service="billing-api", title="Database Connection Failures on billing-api")

    # Direct deployment with bad DB config
    dep_event = append_operational_event(
        inc_id,
        event_type="deployment",
        service="billing-api",
        description="Deployed revision billing-api-00042-xyz with DATABASE_URL update",
        evidence={"revision": "billing-api-00042-xyz", "image": "gcr.io/prj/billing-api:v2"},
    )

    # Corroborating direct error logs
    err_event1 = append_operational_event(
        inc_id,
        event_type="error_log",
        service="billing-api",
        description="HTTP 500: could not translate host name 'db.internal' to address; connection failed",
        evidence={"error_code": "PG_ERR_500", "revision": "billing-api-00042-xyz"},
    )
    err_event2 = append_operational_event(
        inc_id,
        event_type="error_log",
        service="billing-api",
        description="HTTP 500: database connection timeout after 30000ms",
        evidence={"error_code": "TIMEOUT", "revision": "billing-api-00042-xyz"},
    )

    # Run analysis
    import asyncio
    analysis: RootCauseAnalysisResponse = asyncio.run(
        perform_root_cause_analysis(inc_id, force_refresh=True)
    )

    assert analysis.status == "sufficient_evidence"
    assert analysis.insufficient_evidence is False
    assert len(analysis.hypotheses) >= 1

    top_hyp = analysis.hypotheses[0]
    assert top_hyp.rank == 1
    assert top_hyp.suspected_service == "billing-api"
    assert "DATABASE_URL" in top_hyp.suspected_component or "database" in top_hyp.suspected_component.lower()
    assert top_hyp.confidence in ("Confirmed", "Probable")
    assert (dep_event.get("event_id") or dep_event.get("id")) in top_hyp.supporting_evidence_ids
    assert (err_event1.get("event_id") or err_event1.get("id")) in top_hyp.supporting_evidence_ids
    assert len(top_hyp.observed_facts) >= 2
    assert len(top_hyp.recommended_verification_steps) >= 1


def test_conflicting_evidence_penalization():
    """Healthy signals (e.g. 200 OK health check) are recorded as contradicting evidence."""
    inc_id = _create_test_incident(service="payment-api", title="Intermittent Failures")

    append_operational_event(
        inc_id,
        event_type="error_log",
        service="payment-api",
        description="HTTP 500 Internal error",
    )

    # Health check immediately reporting 200 OK healthy
    append_operational_event(
        inc_id,
        event_type="health_check",
        service="payment-api",
        description="Health check returned HTTP 200 OK, latency 45ms",
        evidence={"status": "SERVING", "http_status": 200},
    )

    import asyncio
    analysis = asyncio.run(perform_root_cause_analysis(inc_id, force_refresh=True))

    assert analysis.insufficient_evidence is False
    assert len(analysis.hypotheses) >= 1
    top_hyp = analysis.hypotheses[0]
    assert len(top_hyp.contradicting_evidence) >= 1
    assert any("200" in s or "normal" in s for s in top_hyp.contradicting_evidence)
    # Because of contradicting evidence, it must NOT be claimed as Confirmed
    assert top_hyp.confidence != "Confirmed"


def test_insufficient_evidence_state():
    """When no operational events exist, engine returns 'Insufficient evidence to determine root cause.'"""
    isolated_svc = f"isolated-svc-{uuid.uuid4().hex[:6]}"
    inc_id = _create_test_incident(service=isolated_svc, title="Vague alert with no logs")

    import asyncio
    analysis = asyncio.run(perform_root_cause_analysis(inc_id, force_refresh=True))

    assert analysis.status == "insufficient_evidence"
    assert analysis.insufficient_evidence is True
    assert analysis.summary == "Insufficient evidence to determine root cause."
    assert analysis.hypotheses == []
    assert len(analysis.observed_facts) >= 1
    assert "no operational events" in analysis.observed_facts[0].lower()


def test_invalid_or_fabricated_evidence_ids_stripped():
    """Fabricated or hallucinated evidence IDs must be rejected and stripped from hypotheses."""
    inc_id = _create_test_incident(service="payment-api")
    real_event = append_operational_event(
        inc_id,
        event_type="error_log",
        service="payment-api",
        description="Real error log",
    )
    real_id = real_event.get("event_id") or real_event.get("id")

    from backend.root_cause import build_evidence_catalog
    catalog = build_evidence_catalog(inc_id, [], [real_event])

    cited_ids = [real_id, "fabricated_evt_001", "hallucinated_xyz_999"]
    valid_ids, hydrated, discarded = validate_and_sanitize_evidence_ids(cited_ids, catalog)

    assert valid_ids == [real_id]
    assert len(hydrated) == 1
    assert hydrated[0].evidence_id == real_id
    assert "fabricated_evt_001" in discarded
    assert "hallucinated_xyz_999" in discarded


def test_configured_vs_verified_dependencies():
    """Engine distinguishes verified dependencies from configured ones, flagging assumptions."""
    main_svc = f"order-proc-{uuid.uuid4().hex[:6]}"
    auth_svc = f"auth-gw-{uuid.uuid4().hex[:6]}"
    cache_svc = f"cache-layer-{uuid.uuid4().hex[:6]}"

    inc_id = _create_test_incident(service=main_svc)

    # Ensure services exist
    save_service_node({"name": main_svc, "service_type": "cloud_run", "environment": "production"})
    save_service_node({"name": auth_svc, "service_type": "cloud_run", "environment": "production"})
    save_service_node({"name": cache_svc, "service_type": "cloud_run", "environment": "production"})

    # Dependency 1: auth_svc is VERIFIED
    save_dependency_relationship({
        "source": main_svc,
        "target": auth_svc,
        "relationship_type": "calls_api",
        "evidence": "Observed live token verification calls",
        "verification_status": "verified",
        "confidence": "high",
    })

    # Dependency 2: cache_svc is CONFIGURED only
    save_dependency_relationship({
        "source": main_svc,
        "target": cache_svc,
        "relationship_type": "queries_cache",
        "evidence": "Mentioned in spec doc",
        "verification_status": "configured",
        "confidence": "medium",
    })

    # Events on both upstream dependencies
    append_operational_event(
        inc_id,
        event_type="error_log",
        service=auth_svc,
        description="Auth service 503 unavailable due to OIDC token provider timeout",
    )
    append_operational_event(
        inc_id,
        event_type="error_log",
        service=cache_svc,
        description="Cache cluster memory high warning",
    )

    import asyncio
    analysis = asyncio.run(perform_root_cause_analysis(inc_id, force_refresh=True))

    assert analysis.insufficient_evidence is False
    assert len(analysis.hypotheses) >= 1

    # Unverified assumptions must explicitly call out the unverified status of configured dependencies
    assumptions_text = " ".join(analysis.unverified_assumptions).lower()
    assert "configured" in assumptions_text or "not independently verified" in assumptions_text

    # Verified dependency should have higher ranking or higher confidence than configured dependency
    auth_hyp = next((h for h in analysis.hypotheses if h.suspected_service == auth_svc), None)
    cache_hyp = next((h for h in analysis.hypotheses if h.suspected_service == cache_svc), None)
    if auth_hyp and cache_hyp:
        assert auth_hyp.rank < cache_hyp.rank



def test_ai_unavailable_local_fallback():
    """When Vertex AI / Gemini is unavailable, transparent fallback executes without error."""
    inc_id = _create_test_incident(service="payment-api")
    append_operational_event(
        inc_id,
        event_type="error_log",
        service="payment-api",
        description="Connection refused on port 8080",
    )

    import asyncio
    analysis = asyncio.run(perform_root_cause_analysis(inc_id, force_refresh=True))

    assert analysis.analysis_mode == "deterministic_local_fallback"
    assert analysis.model_name == "tracewell_deterministic_engine_v1"
    assert len(analysis.hypotheses) >= 1


def test_api_get_and_post_root_cause_analysis(test_client):
    """API endpoints GET and POST /incidents/{incident_id}/root-cause-analysis work correctly."""
    inc_id = _create_test_incident(service="payment-api")
    append_operational_event(
        inc_id,
        event_type="error_log",
        service="payment-api",
        description="Database timeout error",
    )

    # POST generates analysis
    resp_post = test_client.post(f"/incidents/{inc_id}/root-cause-analysis")
    assert resp_post.status_code == 200
    data_post = resp_post.json()
    assert data_post["incident_id"] == inc_id
    assert "hypotheses" in data_post
    assert "summary" in data_post

    # GET retrieves cached analysis
    resp_get = test_client.get(f"/incidents/{inc_id}/root-cause-analysis")
    assert resp_get.status_code == 200
    data_get = resp_get.json()
    assert data_get["incident_id"] == inc_id
    assert len(data_get["hypotheses"]) == len(data_post["hypotheses"])


def test_persistence_and_duplicate_prevention():
    """Multiple analysis runs for the same incident update in-place without duplicate records."""
    inc_id = _create_test_incident(service="payment-api")
    append_operational_event(
        inc_id,
        event_type="error_log",
        service="payment-api",
        description="Critical error log 1",
    )

    import asyncio
    asyncio.run(perform_root_cause_analysis(inc_id, force_refresh=True, persist=True))
    asyncio.run(perform_root_cause_analysis(inc_id, force_refresh=True, persist=True))

    # Check persistence
    persisted = get_root_cause_analysis(inc_id)
    assert persisted is not None
    assert persisted["incident_id"] == inc_id

    # Check SQLite table row count
    with _lock, _connect_local() as conn:
        count = conn.execute(
            "SELECT count(*) FROM root_cause_analyses WHERE incident_id = ?",
            (inc_id,),
        ).fetchone()[0]
        assert count == 1, f"Expected exactly 1 row for incident {inc_id}, got {count}"
