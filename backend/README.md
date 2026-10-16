# Tracewell API

See the repository root `README.md` for setup, Google Cloud configuration, and API usage.

Start locally with:

```bash
pip install -r backend/requirements.txt
uvicorn backend.main:app --reload --port 8000
```

API documentation: http://localhost:8000/docs


## Operational events endpoint

`GET /incidents/{incident_id}/events` supports the frontend's Operational Incident
Timeline. It reads a list from the incident's `operational_events` field, or the
legacy `events` field. Each item can contain `id`, `event_type` (or `type`),
`description`, `timestamp` (or `ts`), `service`, and `evidence`.

For an existing incident with no operational events, the endpoint returns `200`
with `[]`. For an unknown incident, it returns `404`. Investigation steps remain
available separately through `/incidents/{incident_id}/steps`.

This endpoint fixes the frontend's repeated 404, but it does not generate live
operational telemetry. The timeline will remain empty until event data is attached
to the incident by fixtures, ingestion, or another integration.
