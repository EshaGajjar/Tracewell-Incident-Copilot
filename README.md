# Tracewell Incident Copilot: Teammate B files

Knowledge base data, embedding ingestion, dashboard skeleton, deck outline.

## Layout
- `data/` 20 knowledge-base docs and 30 past incidents (review and edit by hand)
- `scripts/` ingestion, vector index creation, search test
- `frontend/` React (Vite) dashboard, deployed to Firebase Hosting
- `docs/` Firestore schema and API shapes, deck outline

## Knowledge base (needs A's GCP project and Firestore)
```bash
pip install -r scripts/requirements.txt
export GCP_PROJECT=<project-id> GCP_REGION=us-central1
gcloud auth application-default login
python scripts/ingest.py
./scripts/create_vector_indexes.sh      # wait until the indexes are READY
python scripts/test_search.py "500 errors after deployment"
```
Your account needs Vertex AI User and Firestore access on the project.

## Dashboard
```bash
cd frontend
cp .env.example .env     # fill in the Firebase web app values
npm install
npm run dev
```
Deploy: set the project id in `.firebaserc`, then `cd frontend && npm run build && cd .. && firebase deploy --only hosting`.

## Ticket system
The dashboard works like a small ticketing tool: create a ticket with screenshots, optionally have the Copilot investigate, then close it with root cause and resolution. Closed tickets feed `past_incidents` through the backend `/close` endpoint (Teammate A). See `docs/schema.md`.
Enable Firebase Storage in the console, then `firebase deploy --only firestore:rules,storage,hosting`.

## Day 3-5 additions
- `scripts/seed_incidents.py` seeds 3 sample incidents (resolved, awaiting approval, open ticket)
- `scripts/retrieval_check.py` runs 10 doc searches and 4 incident searches to check retrieval quality
- `scripts/deploy_frontend.ps1` builds and deploys hosting and rules (Windows PowerShell)
- `firestore.rules` lets the browser create plain tickets and add screenshots only; everything else is written by the backend
- `docs/architecture.md`, `docs/deck-slides-1-4.md` for the deck
