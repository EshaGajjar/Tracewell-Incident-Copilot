# Firestore schema and API shapes (draft for Day 1 sync with Teammate A)

## Collections
- `kb_docs/{id}`: id, title, type, service, content, embedding (Vector 768)
- `past_incidents/{id}`: id, service, category, severity, symptom, cause, fix, embedding (Vector 768)
  - The learning loop adds new reports here with the same fields.
- `incidents/{id}`: source (human|copilot), attachments[{name,url}], root_cause, resolution, title, description, service, status, category, severity, created_at, resolved_at,
  diagnosis {root_cause, evidence[{type, detail}], evidence_strength: Confirmed|Probable|Possible},
  pending_action {action, target_revision, reason, status: pending|approved|rejected, decided_at},
  verification {healthy, error_rate, serving_revision, checked_at}, report (string)
- `incidents/{id}/steps/{stepId}`: index (int, ordered), type (tool_call|tool_result|message), tool, input, output, ts

## status values
open (human ticket), new, investigating, awaiting_approval, executing, verifying, resolved, rejected, failed, closed

## API (FastAPI on Cloud Run)
- POST /investigate {incident_id} -> {status}  (the dashboard creates the ticket in Firestore first, then calls this; for a fully new incident A may also accept {title, service, description})
- POST /close {incident_id, root_cause, resolution} -> {status}  (A: save fields, set status=closed, resolved_at, embed symptom+cause+fix and add to `past_incidents` so the agent can reuse it)
- POST /approve {incident_id} -> {status}
- POST /reject {incident_id, reason?} -> {status}
- All endpoints need CORS enabled for the Firebase Hosting origin.

## Needs from A
- `created_at` is a Firestore timestamp on every incident (list view orders by it).
- Steps carry an increasing integer `index` (timeline orders by it).
- Firestore security rules allowing the dashboard to read `incidents` and `steps`.

## Ticket flow (human tickets)
1. Dashboard creates `incidents/{id}` (source=human, status=open) and uploads screenshots to Storage at `incidents/{id}/attachments/`.
2. Optional: "Investigate with Copilot" calls /investigate.
3. Engineer closes the ticket with root cause and resolution via /close; the backend adds it to `past_incidents`.
4. Firestore and Storage rules in the repo are open for the demo. Tighten before real use.
