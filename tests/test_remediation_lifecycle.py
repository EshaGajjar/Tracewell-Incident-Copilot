import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.store import create_local_incident, get_incident

client = TestClient(app)


def test_remediation_lifecycle_full_success():
    """Test full lifecycle: pending -> approved -> executed."""
    inc_id = "inc-lifecycle-test-01"
    create_local_incident(
        inc_id,
        {
            "title": "Payment gateway latency spike",
            "service": "payment-api",
            "severity": "SEV2",
            "category": "latency",
            "status": "awaiting_approval",
            "pending_action": {
                "action": "Roll back payment-api to revision rev-001",
                "reason": "Revision rev-002 introduced connection leak",
                "target_revision": "rev-001",
                "status": "pending",
                "proposed_at": "2026-10-17T12:00:00Z",
            },
        },
    )

    # 1. State: Pending approval
    inc = get_incident(inc_id)
    assert inc["pending_action"]["status"] == "pending"

    # Safeguard verification: Attempting execution before approval must fail (409)
    res_unapproved = client.post("/execute", json={"incident_id": inc_id})
    assert res_unapproved.status_code == 409
    assert "require explicit human operator approval" in res_unapproved.json()["detail"]

    # 2. State: Approve action -> approved_awaiting_execution
    res_approve = client.post(
        "/approve",
        json={"incident_id": inc_id, "reason": "Authorized by SRE on-call"},
    )
    assert res_approve.status_code == 200
    assert res_approve.json()["status"] == "approved_pending_execution"
    assert "No rollback was executed" in res_approve.json()["message"]

    # Verify persisted state
    inc = get_incident(inc_id)
    assert inc["pending_action"]["status"] == "approved"
    assert inc["pending_action"]["lifecycle_state"] == "approved_awaiting_execution"
    assert inc["pending_action"]["decision_note"] == "Authorized by SRE on-call"
    assert inc["pending_action"]["decided_at"] is not None
    assert len(inc.get("action_history", [])) >= 1

    # 3. State: Execute action -> executed successfully
    res_exec = client.post(
        "/execute",
        json={"incident_id": inc_id, "notes": "Executing rollback via Cloud Run API"},
    )
    assert res_exec.status_code == 200
    exec_data = res_exec.json()
    assert exec_data["status"] == "executed"
    assert exec_data["success"] is True
    assert "Successfully executed" in exec_data["message"]
    assert exec_data["executed_at"] is not None
    assert exec_data["evidence"]["target_revision"] == "rev-001"
    assert exec_data["evidence"]["verification_status"] == "verified_healthy"

    # Verify final persisted state
    inc = get_incident(inc_id)
    assert inc["status"] == "mitigated"
    assert inc["pending_action"]["status"] == "executed"
    assert inc["pending_action"]["executed_at"] is not None
    assert inc["pending_action"]["execution_result"] is not None
    assert inc["pending_action"]["execution_evidence"]["target_revision"] == "rev-001"
    assert len(inc["action_history"]) == 2  # approved, executed


def test_remediation_lifecycle_execution_failure_and_retry():
    """Test execution failure simulation and subsequent retry."""
    inc_id = "inc-lifecycle-test-02"
    create_local_incident(
        inc_id,
        {
            "title": "Auth service connection drops",
            "service": "auth-service",
            "severity": "SEV1",
            "category": "outage",
            "status": "awaiting_approval",
            "pending_action": {
                "action": "Restart auth-service pod instances",
                "reason": "Clear thread deadlock",
                "target_revision": "rev-auth-09",
                "status": "pending",
                "proposed_at": "2026-10-17T12:00:00Z",
            },
        },
    )

    # Approve
    res_approve = client.post("/approve", json={"incident_id": inc_id})
    assert res_approve.status_code == 200

    # Execute with failure simulation
    res_fail = client.post(
        "/execute",
        json={
            "incident_id": inc_id,
            "simulate_failure": True,
            "notes": "Kubernetes API timeout during rollout restart",
        },
    )
    assert res_fail.status_code == 200
    fail_data = res_fail.json()
    assert fail_data["status"] == "execution_failed"
    assert fail_data["success"] is False
    assert "Kubernetes API timeout" in fail_data["error"]

    # Verify persisted failure state
    inc = get_incident(inc_id)
    assert inc["status"] == "remediation_failed"
    assert inc["pending_action"]["status"] == "failed"
    assert inc["pending_action"]["failed_at"] is not None
    assert "Kubernetes API timeout" in inc["pending_action"]["error_message"]

    # Retry execution (safeguard allows retry since action was previously approved)
    res_retry = client.post(
        "/execute",
        json={"incident_id": inc_id, "notes": "Retrying after API recovery"},
    )
    assert res_retry.status_code == 200
    retry_data = res_retry.json()
    assert retry_data["status"] == "executed"
    assert retry_data["success"] is True

    # Verify final recovery
    inc = get_incident(inc_id)
    assert inc["status"] == "mitigated"
    assert inc["pending_action"]["status"] == "executed"


def test_remediation_lifecycle_rejection():
    """Test rejection lifecycle."""
    inc_id = "inc-lifecycle-test-03"
    create_local_incident(
        inc_id,
        {
            "title": "Search index lag",
            "service": "search-indexer",
            "severity": "SEV3",
            "category": "performance",
            "status": "awaiting_approval",
            "pending_action": {
                "action": "Purge search elasticsearch cache",
                "reason": "Suspected stale index cache",
                "status": "pending",
                "proposed_at": "2026-10-17T12:00:00Z",
            },
        },
    )

    # Reject
    res_reject = client.post(
        "/reject",
        json={"incident_id": inc_id, "reason": "Cache purge would cause cold-start spike"},
    )
    assert res_reject.status_code == 200
    assert res_reject.json()["status"] == "rejected"

    # Verify persisted state
    inc = get_incident(inc_id)
    assert inc["status"] == "rejected"
    assert inc["pending_action"]["status"] == "rejected"
    assert inc["pending_action"]["decided_at"] is not None
    assert inc["pending_action"]["decision_note"] == "Cache purge would cause cold-start spike"

    # Execution of rejected action must be blocked (409)
    res_exec_rejected = client.post("/execute", json={"incident_id": inc_id})
    assert res_exec_rejected.status_code == 409
