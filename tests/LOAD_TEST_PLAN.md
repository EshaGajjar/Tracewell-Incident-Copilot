# Tracewell incident load test

## What it exercises

`tests/load_test_incidents.py` is an HTTP-level load/seeding script for a running Tracewell API. It creates incidents through `POST /incidents`, resolves incidents through `POST /close` with both `root_cause` and `resolution`, then optionally verifies each incident through `GET /incidents/{incident_id}`. It checks `/health` first and writes a JSON report with request latency, throughput, HTTP errors, and incident IDs.

The scenarios cover deployments, database pool exhaustion, upstream timeouts, token validation, queue backlog, configuration mistakes, memory pressure, and health-check behavior across multiple services.

## Suggested staged test ladder

1. **Smoke:** 5 incidents, concurrency 1.
2. **Small batch:** 25 incidents, concurrency 3.
3. **Normal load:** 100 incidents, concurrency 5.
4. **Higher load:** 500 incidents, concurrency 10.
5. **Stress:** 1,000 incidents, concurrency 20 — only after reviewing earlier results and checking database quotas/cost.

Use a local database or a dedicated staging project. Do not run this against production; the script refuses non-local hosts unless `--allow-remote` is supplied and also refuses hostnames containing `prod` or `production`.

## Commands

```bash
# From repository root; start the backend first.
python tests/load_test_incidents.py --base-url http://127.0.0.1:8000 --count 5 --concurrency 1 --verify

python tests/load_test_incidents.py --base-url http://127.0.0.1:8000 --count 100 --concurrency 5 --verify

# Staging only:
python tests/load_test_incidents.py --base-url https://YOUR-STAGING-HOST --count 250 --concurrency 10 --verify --allow-remote --output staging-load-report.json
```

## Important semantics

- `--resolve-ratio 1.0` (default) means every successfully created incident is closed with root-cause and resolution text.
- Use `--resolve-ratio 0.5` to leave approximately half of incidents open for list/detail/timeline testing.
- The script does not invoke `/investigate` by default. That endpoint may call an LLM/Vertex AI, be much slower, and incur usage charges. Run investigation stress separately after the create/close path is stable.
- These are seeded synthetic incident records and simulated remediation narratives, not real production events or proof that infrastructure was repaired.
- For true concurrency/throughput testing, monitor API logs, CPU/memory, Firestore quotas/latency, and errors in parallel. A high request count alone is not a complete performance test.

## Test cases / acceptance criteria

| ID | Test | Expected result |
|---|---|---|
| LT-01 | API health preflight | `/health` returns 200 before test begins |
| LT-02 | Create one incident | `POST /incidents` returns an incident ID |
| LT-03 | Create many incidents concurrently | Each successful create returns a unique ID; no cross-incident data mixing |
| LT-04 | Attach resolution data | `POST /close` returns `closed` for each selected incident |
| LT-05 | Persist root cause | `GET /incidents/{id}` returns non-empty `root_cause` |
| LT-06 | Persist resolution | `GET /incidents/{id}` returns non-empty `resolution` and `status=closed` |
| LT-07 | Leave open incidents | With a lower resolve ratio, remaining incidents stay open |
| LT-08 | Incident list visibility | Created IDs appear in `GET /incidents` |
| LT-09 | Timeline compatibility | `GET /incidents/{id}/events` and `/steps` respond without server errors |
| LT-10 | Performance report | Report includes p50/p95/p99 latency, throughput, failure count, and error stages |
| LT-11 | Failure behavior | Any non-2xx create/close/detail request is captured in the JSON report |
| LT-12 | Guardrails | Remote host requires explicit `--allow-remote`; production-like host is rejected |
