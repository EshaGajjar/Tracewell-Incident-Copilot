"""Seed 3 fake incidents (with steps) so the dashboard has live data before the agent exists.

Usage:  set GCP_PROJECT=your-project-id   (PowerShell: $env:GCP_PROJECT="your-project-id")
        python scripts/seed_incidents.py
Re-running overwrites the same 3 documents (ids seed_01..seed_03). Uses Admin credentials, so rules don't block it.
"""
import os
from google.cloud import firestore

db = firestore.Client(project=os.environ["GCP_PROJECT"])
TS = firestore.SERVER_TIMESTAMP

def steps(inc_id, items):
    col = db.collection("incidents").document(inc_id).collection("steps")
    for i, (tool, inp, out) in enumerate(items):
        col.document(f"s{i:02d}").set({"index": i, "type": "tool_result", "tool": tool, "input": inp, "output": out, "ts": TS})

# 1) Fully resolved incident (shows diagnosis, verification, report)
db.collection("incidents").document("seed_01").set({
    "title": "Payments failing with 500 errors", "service": "payment-api", "severity": "SEV2",
    "category": "config_error", "source": "copilot", "status": "resolved",
    "description": "/pay returns 500 since the last deploy.", "attachments": [], "created_at": TS,
    "diagnosis": {"root_cause": "Revision 2 deployed with a malformed DATABASE_URL.", "evidence_strength": "Confirmed",
        "evidence": [{"type": "deployment_timing", "detail": "Errors began 2 minutes after revision payment-api-00002 was created"},
                     {"type": "log_error", "detail": "'DATABASE_URL invalid' on every /pay request"},
                     {"type": "past_incident", "detail": "inc_01: malformed DATABASE_URL after deploy, fixed by rollback"},
                     {"type": "config_doc", "detail": "doc_02: required format postgresql://USER:PASS@HOST:5432/DBNAME"}]},
    "pending_action": {"action": "rollback to payment-api-00001", "reason": "Previous revision healthy", "status": "approved"},
    "verification": {"healthy": True, "error_rate": 0.0, "serving_revision": "payment-api-00001"},
    "root_cause": "Malformed DATABASE_URL in revision 2", "resolution": "Rolled back to payment-api-00001",
    "report": "Issue: /pay failing. Impact: all payments. Cause: bad DATABASE_URL. Resolution: rollback. Prevention: validate env vars before deploy.",
})
steps("seed_01", [
    ("get_deployment_info", {"service": "payment-api"}, "Revision 00002 created 14:02, traffic 100%; revision 00001 healthy"),
    ("get_logs", {"service": "payment-api", "minutes": 30}, "ERROR DATABASE_URL invalid (x214)"),
    ("search_past_incidents", {"query": "500 after deploy DATABASE_URL"}, "inc_01, inc_02 (both fixed by rollback)"),
    ("propose_action", {"action": "rollback"}, "Pending approval recorded"),
])

# 2) Waiting for human approval (shows Approve/Reject)
db.collection("incidents").document("seed_02").set({
    "title": "Payments very slow", "service": "payment-api", "severity": "SEV3", "category": "dependency_failure",
    "source": "copilot", "status": "awaiting_approval", "description": "/pay takes over 5 seconds.",
    "attachments": [], "created_at": TS,
    "diagnosis": {"root_cause": "REDIS_URL points to the wrong port, causing connection retries.", "evidence_strength": "Probable",
        "evidence": [{"type": "log_error", "detail": "'Redis connection refused' on every request"},
                     {"type": "deployment_timing", "detail": "Latency rose right after revision 00003"},
                     {"type": "past_incident", "detail": "inc_03: wrong REDIS_URL port causing slow /pay"}]},
    "pending_action": {"action": "rollback to payment-api-00002", "reason": "Last revision before the Redis config change", "status": "pending"},
})
steps("seed_02", [
    ("get_deployment_info", {"service": "payment-api"}, "Revision 00003 created 15:10"),
    ("get_logs", {"service": "payment-api", "minutes": 30}, "Redis connection refused (x180)"),
    ("search_knowledge", {"query": "Redis connection refused"}, "doc_03, doc_14"),
])

# 3) Human ticket, not yet investigated
db.collection("incidents").document("seed_03").set({
    "title": "New payment-api revision not serving", "service": "payment-api", "severity": "SEV2", "source": "human",
    "status": "open", "description": "The latest revision fails to start and requests are erroring.",
    "attachments": [], "created_at": TS,
})
print("Seeded seed_01, seed_02, seed_03")
