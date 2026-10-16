from fastapi.testclient import TestClient
from backend.main import app
from backend.store import create_local_incident

client = TestClient(app)

def setup_function():
    create_local_incident("test_incident", {
        "title":"Payments failing with 500 errors", "service":"payment-api",
        "description":"/pay returns 500 after deploy; DATABASE_URL invalid", "severity":"SEV2",
        "category":"config_error", "source":"human", "status":"open", "attachments":[]
    })

def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"

def test_investigate_returns_structured_fallback():
    r = client.post("/investigate", json={"incident_id":"test_incident"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "awaiting_approval"
    assert body["mode"] == "local_fallback"
    assert body["diagnosis"]["evidence_strength"] == "Possible"
    assert body["past_incident_results"] > 0

def test_approve_records_decision_but_does_not_execute():
    client.post("/investigate", json={"incident_id":"test_incident"})
    r = client.post("/approve", json={"incident_id":"test_incident"})
    assert r.status_code == 200
    assert r.json()["status"] == "approved_pending_execution"
    assert "No rollback was executed" in r.json()["message"]
