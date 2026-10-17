"""Automated test suite verifying:
1. Timeline deduplication (tool calls vs completed results, lifecycle events, retries, summaries).
2. Data safety, backup creation, and local SQLite data reset.
3. All 10 distinct edge-case incident investigation scenarios.
"""
from __future__ import annotations

import os
import sqlite3
import uuid
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from backend.main import app, _active_investigations, _active_investigations_lock
from backend.timeline import filter_timeline_steps, generate_step_summary, group_steps_by_run
from backend.store import (
    _local_db_path,
    append_operational_event,
    append_step,
    create_local_incident,
    get_incident,
    list_operational_events,
    list_steps,
)
from scripts.reset_and_seed_local_incidents import reset_and_seed_local_test_data, SCENARIOS


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


# ===================================================================
# Task 1: Timeline Deduplication & Lifecycle Event Tests
# ===================================================================

def test_timeline_tool_call_start_hidden_when_result_exists():
    """Verify started tool call without evidence is hidden when paired result exists."""
    steps = [
        {
            "index": 0,
            "type": "tool_call",
            "tool": "get_logs",
            "input": {"service": "payment-api"},
            "output": {"status": "started"},
            "execution_id": "exec_test_01",
            "ts": "2026-10-17T12:00:00Z",
        },
        {
            "index": 1,
            "type": "tool_result",
            "tool": "get_logs",
            "input": {"service": "payment-api"},
            "output": {"logs": [{"message": "Connection reset"}]},
            "execution_id": "exec_test_01",
            "ts": "2026-10-17T12:00:01Z",
        },
    ]

    visible = filter_timeline_steps(steps)
    assert len(visible) == 1
    assert visible[0]["type"] == "tool_result"
    assert visible[0]["execution_id"] == "exec_test_01"
    assert "logs" in visible[0]["output"]


def test_timeline_tool_call_without_result_remains_visible():
    """Verify an in-flight or uncompleted tool call without result is not hidden."""
    steps = [
        {
            "index": 0,
            "type": "tool_call",
            "tool": "check_service_health",
            "input": {"service": "payment-api"},
            "output": {"status": "started"},
            "execution_id": "exec_inflight_01",
            "ts": "2026-10-17T12:00:00Z",
        }
    ]

    visible = filter_timeline_steps(steps)
    assert len(visible) == 1
    assert visible[0]["type"] == "tool_call"
    assert visible[0]["execution_id"] == "exec_inflight_01"


def test_timeline_genuine_retries_remain_distinguishable():
    """Verify distinct retry executions with different execution IDs remain visible."""
    steps = [
        # First execution (failed with timeout)
        {
            "index": 0,
            "type": "tool_call",
            "tool": "get_logs",
            "input": {"service": "payment-api", "attempt": 1},
            "output": {"status": "started"},
            "execution_id": "exec_retry_1",
            "ts": "2026-10-17T12:00:00Z",
        },
        {
            "index": 1,
            "type": "tool_result",
            "tool": "get_logs",
            "input": {"service": "payment-api", "attempt": 1},
            "output": {"error": "TimeoutError: query took > 5000ms"},
            "execution_id": "exec_retry_1",
            "ts": "2026-10-17T12:00:05Z",
        },
        # Second execution (retry succeeded)
        {
            "index": 2,
            "type": "tool_call",
            "tool": "get_logs",
            "input": {"service": "payment-api", "attempt": 2},
            "output": {"status": "started"},
            "execution_id": "exec_retry_2",
            "ts": "2026-10-17T12:00:06Z",
        },
        {
            "index": 3,
            "type": "tool_result",
            "tool": "get_logs",
            "input": {"service": "payment-api", "attempt": 2},
            "output": {"logs": [{"message": "Found 4 error entries"}]},
            "execution_id": "exec_retry_2",
            "ts": "2026-10-17T12:00:07Z",
        },
    ]

    visible = filter_timeline_steps(steps)
    # Both start steps hidden; both completed results preserved
    assert len(visible) == 2
    assert visible[0]["execution_id"] == "exec_retry_1"
    assert "error" in visible[0]["output"]
    assert visible[1]["execution_id"] == "exec_retry_2"
    assert "logs" in visible[1]["output"]


def test_timeline_preserves_all_lifecycle_events():
    """Verify investigation lifecycle events are strictly preserved."""
    steps = [
        {
            "index": 0,
            "type": "message",
            "tool": "investigation",
            "input": {"incident_id": "inc_test"},
            "output": "Investigation started.",
            "ts": "2026-10-17T12:00:00Z",
        },
        {
            "index": 1,
            "type": "tool_call",
            "tool": "check_service_health",
            "input": {"service": "payment-api"},
            "output": {"status": "started"},
            "execution_id": "exec_hc_01",
            "ts": "2026-10-17T12:00:01Z",
        },
        {
            "index": 2,
            "type": "tool_result",
            "tool": "check_service_health",
            "input": {"service": "payment-api"},
            "output": {"status": "healthy"},
            "execution_id": "exec_hc_01",
            "ts": "2026-10-17T12:00:02Z",
        },
        {
            "index": 3,
            "type": "tool_result",
            "tool": "submit_diagnosis",
            "input": {},
            "output": {"saved": True, "diagnosis": {"root_cause": "Test root cause"}},
            "ts": "2026-10-17T12:00:03Z",
        },
        {
            "index": 4,
            "type": "message",
            "tool": "investigation_completed",
            "input": {},
            "output": "Investigation completed successfully.",
            "ts": "2026-10-17T12:00:04Z",
        },
    ]

    visible = filter_timeline_steps(steps)
    assert len(visible) == 4
    tools = [s["tool"] for s in visible]
    assert "investigation" in tools
    assert "check_service_health" in tools
    assert "submit_diagnosis" in tools
    assert "investigation_completed" in tools


def test_timeline_evidence_summary_generation():
    """Verify result summaries are generated cleanly for completed tools."""
    step_dep = {
        "tool": "get_deployment_info",
        "output": {"revisions": ["rev-1", "rev-2"]},
    }
    assert "2 deployment revision" in generate_step_summary(step_dep)

    step_logs = {
        "tool": "get_logs",
        "output": {"logs": [{"id": "log1"}, {"id": "log2"}, {"id": "log3"}]},
    }
    assert "3 log entries" in generate_step_summary(step_logs)

    step_diag = {
        "tool": "submit_diagnosis",
        "output": {"diagnosis": {"root_cause": "Database connection pool exhausted"}},
    }
    assert "Database connection pool exhausted" in generate_step_summary(step_diag)


# ===================================================================
# Task 2: Data Reset and Safety Tests
# ===================================================================

def test_local_data_reset_safety_and_backup():
    """Verify reset creates a timestamped backup and cleans only local incident data."""
    db_path = _local_db_path()
    assert db_path.exists(), "Local database must exist"

    # Count service catalog rows before reset
    conn = sqlite3.connect(db_path)
    services_before = conn.execute("SELECT count(*) FROM services").fetchone()[0]
    deps_before = conn.execute("SELECT count(*) FROM service_dependencies").fetchone()[0]
    conn.close()

    assert services_before > 0, "Services catalog must not be empty"
    assert deps_before > 0, "Service dependencies must not be empty"

    # Perform safe reset
    res = reset_and_seed_local_test_data()
    assert res["status"] == "success"
    assert res["seeded_count"] == 10

    # Verify backup exists
    backup_path = Path(res["backup_path"])
    assert backup_path.exists(), f"Backup file not found at {backup_path}"
    assert backup_path.stat().st_size > 0, "Backup file must not be empty"

    # Verify incident counts and preservation of shared catalog
    conn = sqlite3.connect(db_path)
    incidents_after = conn.execute("SELECT count(*) FROM incidents").fetchone()[0]
    services_after = conn.execute("SELECT count(*) FROM services").fetchone()[0]
    deps_after = conn.execute("SELECT count(*) FROM service_dependencies").fetchone()[0]
    conn.close()

    assert incidents_after == 10, "Exactly 10 incidents should be seeded"
    assert services_after >= services_before, "Services catalog must be preserved"
    assert deps_after >= deps_before, "Service dependencies must be preserved"


# ===================================================================
# Task 3 & 4: Investigation Scenarios Verification
# ===================================================================

def test_all_10_scenarios_can_be_retrieved_and_investigated(client):
    """Verify all 10 scenarios can be opened, investigated, and reviewed."""
    for sc in SCENARIOS:
        sid = sc["id"]

        # Retrieve incident
        r = client.get(f"/incidents/{sid}")
        assert r.status_code == 200, f"Failed to get incident {sid}"
        inc = r.json()
        assert inc["title"] == sc["title"]
        assert inc["service"] == sc["service"]
        assert inc["severity"] == sc["severity"]

        # Investigate incident
        r_inv = client.post(f"/incidents/{sid}/investigate")
        assert r_inv.status_code == 200, f"Investigation failed for {sid}: {r_inv.text}"

        # Check steps API deduplication
        r_steps = client.get(f"/incidents/{sid}/steps")
        assert r_steps.status_code == 200
        visible_steps = r_steps.json()

        r_raw = client.get(f"/incidents/{sid}/steps?raw=true")
        assert r_raw.status_code == 200
        raw_steps = r_raw.json()

        # Deduplication assertions
        assert len(visible_steps) < len(raw_steps), f"Steps were not deduplicated for {sid}"
        started_in_visible = [s for s in visible_steps if s.get("type") == "tool_call" and s.get("output") == {"status": "started"}]
        assert len(started_in_visible) == 0, f"Started tool calls remained in visible timeline for {sid}"

        # Lifecycle events are present
        tools_in_visible = [s.get("tool") for s in visible_steps]
        assert "investigation" in tools_in_visible
        assert "submit_diagnosis" in tools_in_visible
        assert "investigation_completed" in tools_in_visible

        # Root Cause Analysis check
        r_rca = client.get(f"/incidents/{sid}/root-cause-analysis")
        assert r_rca.status_code == 200
        rca = r_rca.json()

        # Causal Graph check
        r_cg = client.get(f"/incidents/{sid}/causal-graph")
        assert r_cg.status_code == 200
        cg = r_cg.json()
        assert len(cg["nodes"]) > 0
        assert len(cg["edges"]) > 0


def test_scenario_1_deployment_database_failure(client):
    """Scenario 1: Evaluates deployment and database errors without jumping to unconfirmed root cause."""
    sid = "inc-scen-01-pay-deploy-db"
    r = client.get(f"/incidents/{sid}/root-cause-analysis")
    assert r.status_code == 200
    rca = r.json()
    assert len(rca["hypotheses"]) > 0
    top = rca["hypotheses"][0]
    assert "payment-api" in top["suspected_service"]
    assert "database" in top["explanation"].lower() or "connection" in top["explanation"].lower()
    # Contradicting healthy signal is noted
    assert len(top["contradicting_evidence"]) > 0 or len(rca.get("observed_facts", [])) > 0


def test_scenario_2_upstream_database_outage(client):
    """Scenario 2: Identifies orders-db as upstream cause rather than blaming order-service alone."""
    sid = "inc-scen-02-ord-upstream-db"
    r = client.get(f"/incidents/{sid}/root-cause-analysis")
    assert r.status_code == 200
    rca = r.json()
    assert len(rca["hypotheses"]) > 0
    top = rca["hypotheses"][0]
    # Root cause points to orders-db upstream dependency
    assert "orders-db" in top["suspected_service"] or "orders-db" in top["explanation"]


def test_scenario_3_downstream_cascading_failure(client):
    """Scenario 3: Distinguishes payment-api root cause from downstream order-service / frontend symptoms."""
    sid = "inc-scen-03-pay-downstream-cascade"
    r = client.get(f"/incidents/{sid}/correlated-events")
    assert r.status_code == 200
    corr = r.json()
    # Downstream dependents must be classified as downstream_dependent, not causal candidates
    downstream_events = [e for e in corr["events"] if e.get("relationship_direction") == "downstream_dependent"]
    assert len(downstream_events) > 0
    for de in downstream_events:
        assert de["is_causal_candidate"] is False, "Downstream dependent errors must not be marked as causal candidate"


def test_scenario_4_no_deployment_application_errors(client):
    """Scenario 4: Does not invent a deployment-related cause when no deployment occurred."""
    sid = "inc-scen-04-auth-no-deploy-err"
    r = client.get(f"/incidents/{sid}/root-cause-analysis")
    assert r.status_code == 200
    rca = r.json()
    assert len(rca["hypotheses"]) > 0
    top = rca["hypotheses"][0]
    assert "without a preceding deployment" in top["explanation"] or "deployment" not in top["suspected_component"].lower()


def test_scenario_6_temporally_related_unrelated_service(client):
    """Scenario 6: Notification service errors are classified as temporally associated without claiming causation."""
    sid = "inc-scen-06-pay-unrelated-notif"
    r = client.get(f"/incidents/{sid}/correlated-events")
    assert r.status_code == 200
    corr = r.json()
    notif_events = [e for e in corr["events"] if e["service"] == "notification-service"]
    assert len(notif_events) > 0
    assert notif_events[0]["classification"] == "temporally_associated"
    assert "Timestamp proximity alone does not establish causation" in notif_events[0]["correlation_reason"]


def test_scenario_7_duplicate_log_ingestion_idempotency(client):
    """Scenario 7: Re-ingesting the same log error with stable dedupe_key is idempotent."""
    sid = "inc-scen-07-pay-dup-log-ingest"
    # Current operational events
    events_before = list_operational_events(sid)
    count_before = len(events_before)

    # Attempt duplicate insert of existing dedupe_key
    append_operational_event(
        sid,
        event_type="error_log",
        service="payment-api",
        description="Duplicate insert attempt with same dedupe_key",
        event_timestamp="2026-10-17T11:00:00Z",
        evidence={"insert_id": "log-dup-err-998877"},
        dedupe_key="log-dup-err-998877",
    )

    events_after = list_operational_events(sid)
    assert len(events_after) == count_before, "Duplicate log insert must be deduplicated idempotently"


def test_scenario_8_missing_revision_uncertainty(client):
    """Scenario 8: Missing revision produces explicit uncertainty in root cause analysis."""
    sid = "inc-scen-08-ord-missing-revision"
    r = client.get(f"/incidents/{sid}/root-cause-analysis")
    assert r.status_code == 200
    rca = r.json()
    assert len(rca["hypotheses"]) > 0
    top = rca["hypotheses"][0]
    assert top["confidence"] == "Possible"
    assert "uncertainty" in top["confidence_explanation"].lower() or "incomplete" in top["confidence_explanation"].lower()


def test_scenario_10_insufficient_evidence_explicit_result(client):
    """Scenario 10: Returns explicit insufficient evidence without inventing a root cause."""
    sid = "inc-scen-10-auth-insufficient-telemetry"
    r = client.get(f"/incidents/{sid}/root-cause-analysis")
    assert r.status_code == 200
    rca = r.json()
    assert rca["insufficient_evidence"] is True
    assert rca["status"] == "insufficient_evidence"
    assert "Insufficient evidence to determine root cause" in rca["summary"]
    assert len(rca["hypotheses"]) == 0


# ===================================================================
# Task: Re-Run Investigation, History Preservation & Concurrency Tests
# ===================================================================

def test_rerun_investigation_assigns_unique_run_ids_and_preserves_history(client):
    """Verify that re-running an investigation assigns unique run_ids, preserves history,
    and returns grouped runs with the latest run first.
    """
    sid = f"inc-test-rerun-{uuid.uuid4().hex[:8]}"
    create_local_incident(
        incident_id=sid,
        fields={
            "title": "Test Incident for Re-run Investigation",
            "description": "Testing run_id association and history preservation",
            "service": "payment-api",
            "severity": "SEV2",
        },
    )

    # 1. First investigation run
    r1 = client.post("/investigate", json={"incident_id": sid, "run_id": "run_test_001"})
    assert r1.status_code == 200, f"Run 1 failed: {r1.text}"
    assert r1.json().get("run_id") == "run_test_001"

    # Steps after run 1
    steps_r1 = client.get(f"/incidents/{sid}/steps?raw=false").json()
    assert len(steps_r1) > 0
    for s in steps_r1:
        assert s.get("run_id") == "run_test_001"

    # Verify single run group
    runs_after_1 = client.get(f"/incidents/{sid}/steps?group_by_run=true").json()
    assert len(runs_after_1) == 1
    assert runs_after_1[0]["run_id"] == "run_test_001"
    assert runs_after_1[0]["is_latest"] is True
    assert runs_after_1[0]["label"] == "Run #1 (Latest)"

    # 2. Re-run investigation with second run_id
    r2 = client.post("/investigate", json={"incident_id": sid, "run_id": "run_test_002"})
    assert r2.status_code == 200, f"Run 2 failed: {r2.text}"
    assert r2.json().get("run_id") == "run_test_002"

    # Verify all historical steps are preserved (step count increased)
    all_steps = client.get(f"/incidents/{sid}/steps?raw=false").json()
    assert len(all_steps) > len(steps_r1), "Re-run must preserve previous run steps, not overwrite them"

    # Verify steps can be queried per specific run_id
    only_run1 = client.get(f"/incidents/{sid}/steps?run_id=run_test_001").json()
    assert len(only_run1) == len(steps_r1)
    for s in only_run1:
        assert s.get("run_id") == "run_test_001"

    only_run2 = client.get(f"/incidents/{sid}/steps?run_id=run_test_002").json()
    assert len(only_run2) > 0
    for s in only_run2:
        assert s.get("run_id") == "run_test_002"

    # 3. Verify grouped runs: latest run (run_test_002) is first and clearly labeled
    grouped_runs = client.get(f"/incidents/{sid}/steps?group_by_run=true").json()
    assert len(grouped_runs) == 2

    # Latest run is FIRST
    latest_run = grouped_runs[0]
    assert latest_run["run_id"] == "run_test_002"
    assert latest_run["is_latest"] is True
    assert latest_run["run_number"] == 2
    assert "Latest" in latest_run["label"]
    assert latest_run["status"] in {"completed", "in_progress"}
    assert len(latest_run["steps"]) == len(only_run2)

    # Previous run is preserved as second
    historical_run = grouped_runs[1]
    assert historical_run["run_id"] == "run_test_001"
    assert historical_run["is_latest"] is False
    assert historical_run["run_number"] == 1
    assert "Run #1" in historical_run["label"]
    assert len(historical_run["steps"]) == len(only_run1)


def test_group_steps_by_run_displays_latest_first():
    """Verify group_steps_by_run groups steps, infers legacy boundaries, and displays latest first."""
    sample_steps = [
        # Run 1 steps
        {"run_id": "run_alpha", "tool": "investigation", "type": "message", "output": "Investigation started.", "ts": "2026-10-17T10:00:00Z"},
        {"run_id": "run_alpha", "tool": "get_logs", "type": "tool_result", "output": {"logs": []}, "ts": "2026-10-17T10:00:01Z"},
        {"run_id": "run_alpha", "tool": "investigation_completed", "type": "message", "output": "Done", "ts": "2026-10-17T10:00:05Z"},
        # Run 2 steps
        {"run_id": "run_beta", "tool": "investigation", "type": "message", "output": "Investigation started.", "ts": "2026-10-17T11:00:00Z"},
        {"run_id": "run_beta", "tool": "check_service_health", "type": "tool_result", "output": {"status": "ok"}, "ts": "2026-10-17T11:00:02Z"},
        {"run_id": "run_beta", "tool": "submit_diagnosis", "type": "diagnosis", "output": {"diagnosis": {"root_cause": "Bug"}}, "ts": "2026-10-17T11:00:03Z"},
    ]

    grouped = group_steps_by_run(sample_steps)
    assert len(grouped) == 2

    # Latest run is first (Run #2 / run_beta)
    assert grouped[0]["run_id"] == "run_beta"
    assert grouped[0]["is_latest"] is True
    assert grouped[0]["run_number"] == 2
    assert "Latest" in grouped[0]["label"]
    assert grouped[0]["diagnosis"] == {"root_cause": "Bug"}
    assert len(grouped[0]["steps"]) == 3

    # Historical run is second (Run #1 / run_alpha)
    assert grouped[1]["run_id"] == "run_alpha"
    assert grouped[1]["is_latest"] is False
    assert grouped[1]["run_number"] == 1
    assert "Run #1" in grouped[1]["label"]
    assert len(grouped[1]["steps"]) == 3


def test_investigation_concurrency_lock_prevents_duplicate_runs(client):
    """Verify that attempting to trigger a concurrent investigation on the same incident
    returns HTTP 409 Conflict to prevent duplicate submissions.
    """
    sid = f"inc-test-concurrency-{uuid.uuid4().hex[:8]}"
    create_local_incident(
        incident_id=sid,
        fields={
            "title": "Concurrency Test Incident",
            "description": "Testing duplicate submission prevention",
            "service": "payment-api",
        },
    )

    # Simulate an active investigation in progress
    with _active_investigations_lock:
        _active_investigations.add(sid)

    try:
        # POST /investigate should fail with 409
        r = client.post("/investigate", json={"incident_id": sid})
        assert r.status_code == 409
        assert "already in progress" in r.json()["detail"]

        # Path-based POST /incidents/{sid}/investigate should also fail with 409
        r_path = client.post(f"/incidents/{sid}/investigate")
        assert r_path.status_code == 409

        # Stream endpoint should also fail with 409
        r_stream = client.get(f"/incidents/{sid}/investigate/stream")
        assert r_stream.status_code == 409
    finally:
        with _active_investigations_lock:
            _active_investigations.discard(sid)

    # After lock release, normal investigation succeeds
    r_ok = client.post("/investigate", json={"incident_id": sid})
    assert r_ok.status_code == 200


def test_tool_execution_retry_deduplication():
    """Verify that retrying a tool execution with the same execution_id does not create duplicate entries."""
    sid = f"inc-test-retry-{uuid.uuid4().hex[:8]}"
    exec_id = f"exec_retry_{uuid.uuid4().hex[:8]}"

    # Step call
    s1 = append_step(
        sid,
        step_type="tool_call",
        tool="get_deployment_info",
        input_data={"service": "payment-api"},
        output_data={"status": "started"},
        execution_id=exec_id,
        run_id="run_retry_test",
    )
    # Duplicate call attempt with identical execution_id and tool
    s1_dup = append_step(
        sid,
        step_type="tool_call",
        tool="get_deployment_info",
        input_data={"service": "payment-api"},
        output_data={"status": "started"},
        execution_id=exec_id,
        run_id="run_retry_test",
    )
    assert s1["execution_id"] == s1_dup["execution_id"]

    # Result
    s2 = append_step(
        sid,
        step_type="tool_result",
        tool="get_deployment_info",
        input_data={"service": "payment-api"},
        output_data={"revisions": ["rev-1"]},
        execution_id=exec_id,
        run_id="run_retry_test",
    )
    # Duplicate result attempt
    s2_dup = append_step(
        sid,
        step_type="tool_result",
        tool="get_deployment_info",
        input_data={"service": "payment-api"},
        output_data={"revisions": ["rev-1"]},
        execution_id=exec_id,
        run_id="run_retry_test",
    )
    assert s2["execution_id"] == s2_dup["execution_id"]

    # Verify timeline filter deduplicates exact fingerprints and pairs start/result
    raw_steps = list_steps(sid)
    filtered = filter_timeline_steps(raw_steps)
    # Only 1 tool_result step should be visible for this execution_id (start hidden, duplicates stripped)
    exec_steps = [s for s in filtered if s.get("execution_id") == exec_id]
    assert len(exec_steps) == 1
    assert exec_steps[0]["type"] == "tool_result"

