# Deck draft: slides 1-4

## 1. Title
Tracewell Incident Copilot: an AI first-level incident investigator. Category: Future of Work and Enterprise Productivity. Team of 2.

## 2. Problem
- Engineers lose the first 15-30 minutes of an incident gathering evidence from logs, deployments and runbooks.
- Knowledge from past incidents sits in tickets nobody searches.
- Risky fixes (rollbacks) need care, so teams either move slowly or act without a check.
(Add one real number for time lost per incident if you have a source.)

## 3. Solution
Investigate, reason, recommend, human approval, act, verify, document, learn.
- The agent gathers deployment, log and health evidence and searches docs and past incidents.
- It grades the root cause by evidence strength (Confirmed, Probable, Possible).
- A human approves the action; rollback runs in code, not in the model.
- Closed tickets are embedded back into the knowledge base so the next incident benefits.

## 4. Architecture
Use the diagram in docs/architecture.md. Stack: ADK + Gemini on Vertex AI, FastAPI on Cloud Run, Firestore vector search, React on Firebase Hosting, Cloud Logging.
