# Tracewell manual incident evidence fix

This is a targeted patch for the source files you uploaded. It does not include `.env`, local databases, credentials, or build artifacts.

## What it changes

- Persists every manual incident report as a `user_report` operational event.
- Accepts up to five text attachments (`.txt`, `.log`, `.json`, `.csv`), limits each to 1 MB and 12,000 characters, and stores their content as evidence events.
- Adds the ADK tool `get_reporter_evidence`, and instructs the investigator to inspect reporter-provided evidence before drawing conclusions.
- Captures ingestion status and counts in the incident record and investigation response rather than silently losing ingestion failures.
- Displays evidence collection counts and GCP ingestion errors in the incident detail UI.
- Keeps the existing Cloud Run / Firestore / Gemini architecture and investigation route.

Screenshots/images are intentionally not accepted as evidence in this patch because the existing backend does not perform image OCR or vision analysis. The UI now says this clearly instead of implying an image was analyzed.

## Apply the patch

From your local `tracewell-timeline` project root, copy these files into the matching locations, replacing the existing files:

- `backend/main.py`
- `backend/agent.py`
- `backend/tools.py`
- `frontend/src/components/ReportForm.jsx`
- `frontend/src/components/IncidentDetail.jsx`

Keep your existing environment files and other source files unchanged.

## Validate locally

```bash
python3 -m py_compile backend/main.py backend/agent.py backend/tools.py
cd frontend
npm run build
cd ..
```

## Deploy

Deploy the backend from the project root using the same Cloud Run command you already use:

```bash
gcloud run deploy tracewell-api --source . --region us-central1 --project project-2e2620c1-48ee-49f0-ba8
```

Then deploy the frontend from the directory where your Firebase Hosting configuration is located. Make sure that configuration's `public` directory points at `frontend/dist` if deploying from the project root, or `dist` if deploying from `frontend`:

```bash
cd frontend
npm run build
npx --yes firebase-tools deploy --only hosting
```

Only deploy from `frontend` if `frontend/firebase.json` exists and is configured to publish `dist`. Otherwise, run the Firebase command from the directory containing the correct `firebase.json`.

## Behavior note

GCP ingestion can legitimately return zero revisions or logs (for example, if the service name does not match a Cloud Run service or the time window has no errors). Tracewell will now expose that gap. It does not manufacture telemetry or label inferred hypotheses as confirmed evidence.
