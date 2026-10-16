# Tracewell Incident Copilot

AI-first incident investigation with evidence-backed diagnosis, human-reviewed remediation proposals, and a React dashboard.

## Repository layout

- `frontend/` — existing React + Vite dashboard (Firebase Hosting / Firestore)
- `backend/` — Python FastAPI API, Google ADK agent, retrieval tools, Cloud Run/Cloud Logging tools
- `data/` — 20 knowledge documents and 30 historical incident examples
- `scripts/` — ingestion, vector-index, retrieval-check and seed utilities
- `docs/` — architecture, schema, and pitch materials

## Priority 1: FastAPI + ADK backend

### Local development without Google Cloud

This mode is useful for testing the API and frontend wiring. It uses the checked-in JSON knowledge base and historical incidents, and stores incident state in memory. **It does not query live Cloud Logging/Cloud Run and must not be presented as a real rollback demo.**

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\\Scripts\\activate
pip install -r backend/requirements.txt
cp backend/.env.example backend/.env
# Optional: copy values into your shell, or use a root .env loaded by your environment manager.
uvicorn backend.main:app --reload --port 8000
```

API docs: <http://localhost:8000/docs>

Test the backend:

```bash
pytest
```

Create a local test incident, then investigate it:

```bash
curl -X POST http://localhost:8000/incidents \\
  -H 'Content-Type: application/json' \\
  -d '{"title":"Payments failing after deploy","service":"payment-api","description":"/pay returns 500 after deploy; DATABASE_URL is invalid","severity":"SEV2","category":"config_error"}'

# Replace INCIDENT_ID with incident_id from the previous response.
curl -X POST http://localhost:8000/investigate \\
  -H 'Content-Type: application/json' -d '{"incident_id":"INCIDENT_ID"}'
```

### Google Cloud / Vertex AI mode

1. Select a Google Cloud project and enable billing.
2. Enable Cloud Run, Cloud Logging, Firestore, Vertex AI and the APIs required by your deployment.
3. Set Application Default Credentials: `gcloud auth application-default login`.
4. Configure environment variables:

```bash
export GCP_PROJECT="your-project-id"
export GCP_REGION="us-central1"
export GEMINI_MODEL="gemini-2.5-flash"
export GOOGLE_GENAI_USE_VERTEXAI="true"
export CORS_ORIGINS="http://localhost:5173,https://YOUR_FIREBASE_HOSTING_ORIGIN"
```

5. Create a Firestore Native database and run the existing ingestion process after creating the vector indexes:

```bash
pip install -r scripts/requirements.txt
python scripts/ingest.py
./scripts/create_vector_indexes.sh
python scripts/test_search.py "500 errors after deployment"
```

The backend expects `kb_docs` and `past_incidents` documents to contain the `embedding` vector field. When vector search errors, the retrieval layer uses the checked-in JSON corpus as a fallback and reports that fallback method in the tool result.

The Cloud Run service account should receive only the required permissions for Vertex AI, Firestore, Cloud Logging read access, and Cloud Run service/revision inspection. Add traffic-update permission only when Priority 2 implements the separate approval execution endpoint. Do not give the agent tool direct rollback access.

### Connect the frontend

Set `VITE_API_URL` in `frontend/.env` to the API base URL, for example:

```dotenv
VITE_API_URL=http://localhost:8000
```

Restart Vite after editing environment variables. The browser origin must be present in backend `CORS_ORIGINS`.

## Implemented endpoints

- `GET /health` — backend mode and configuration status
- `POST /incidents` — optional API-based incident creation
- `POST /investigate` — starts the ADK investigation and persists tool calls/results and structured diagnosis
- `POST /approve` — records a decision only; it does **not** execute rollback in this milestone
- `POST /reject` — records a rejection
- `POST /close` — closes a ticket; the closed-incident embedding/learning loop is a later milestone

### Agent tools in Priority 1

- `search_knowledge(query)` — Firestore vector search with JSON lexical fallback
- `search_past_incidents(query)` — Firestore vector search with JSON lexical fallback
- `get_deployment_info(service)` — read-only Cloud Run service/revision/traffic inspection
- `get_logs(service, minutes)` — read-only Cloud Logging query
- `check_service_health(service)` — read-only HTTP health probe
- `propose_action(action, reason, target_revision)` — persists a pending action; no execution
- `submit_diagnosis(...)` — persists the structured diagnosis and evidence grade

The ADK agent does not receive a rollback or traffic-mutation tool. Approval is recorded separately. Priority 2 must implement authenticated approval and safe execution, followed by actual verification.

## Important limitations

- Local fallback state is in-memory and disappears when the API process restarts.
- ADK/Vertex AI requires Google Cloud credentials and a configured project.
- Cloud Run revision and Logging tools have not been validated against your live project from this environment.
- Vector retrieval requires successful ingestion and Firestore vector indexes; lexical fallback is not semantic retrieval.
- The prototype's evidence strength is a rubric, not a calibrated probability. Review diagnosis before remediation.
- Before real use, secure Firestore/Storage rules, authenticate approvers, restrict CORS, and avoid placing credentials or sensitive incident data in the repository.


## Local frontend configuration

Create `frontend/.env` from `frontend/.env.example`, fill in the Firebase settings, and keep the backend URL as a plain URL:

```env
VITE_API_URL=http://localhost:8000
```

Do not include Markdown link syntax around the URL. Restart the Vite dev server after changing `.env`. The incident form now displays the current submission stage and surfaces timeout/network errors instead of leaving the buttons spinning indefinitely.

## Local persistence and demo dashboard

When `GCP_PROJECT` is not configured, the backend uses SQLite for local fallback persistence. Incidents and investigation timeline steps are stored in `backend/tracewell_local.sqlite3`, so they survive browser refreshes and backend restarts. To use a different path, set `TRACEWELL_LOCAL_DB` to an absolute or home-relative file path before starting the API. The database file is intentionally git-ignored and is created at runtime; keep it if you want to retain your local demo history.

The dashboard refreshes incident counts and the incident feed automatically. Local fallback mode still does not connect to live Cloud Logging or Cloud Run, and the ADK investigation path requires its dependencies and credentials to be configured separately.
