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

def test_investigation_stages_endpoint():
    r = client.get("/investigation/stages")
    assert r.status_code == 200
    stages = r.json()
    assert len(stages) == 6
    expected_labels = [
        "Retrieving incident details",
        "Collecting operational events",
        "Correlating logs and deployment events",
        "Analyzing service dependencies",
        "Evaluating root-cause hypotheses",
        "Preparing the evidence-backed diagnosis",
    ]
    assert [s["label"] for s in stages] == expected_labels

def test_investigate_stream_events():
    r = client.get("/incidents/test_incident/investigate/stream")
    assert r.status_code == 200
    assert "text/event-stream" in r.headers.get("content-type", "")
    lines = [l for l in r.text.split("\n") if l.startswith("data: ")]
    assert len(lines) >= 6
    import json
    events = [json.loads(l[6:]) for l in lines]
    stage_keys = [e["stage"] for e in events]
    assert "retrieving_details" in stage_keys
    assert "collecting_events" in stage_keys
    assert "correlating_events" in stage_keys
    assert "analyzing_dependencies" in stage_keys
    assert "evaluating_hypotheses" in stage_keys
    assert "preparing_diagnosis" in stage_keys
    assert "complete" in stage_keys

def test_investigate_stream_404_for_missing_incident():
    r = client.get("/incidents/non_existent_incident_id/investigate/stream")
    assert r.status_code == 404

