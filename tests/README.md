# Tracewell dependency-aligned scenarios

These scenarios are aligned to the service names visible in the supplied graph:
`auth-cache`, `auth-service`, `firestore`, `notification-service`, `order-service`,
`payment-api`, `payment-cache`, and `payment-db`.

## Run

Start the backend, then from this folder run:

```bash
python tests/run_graph_scenarios.py --count 6 --concurrency 1
```

For a larger staged batch:

```bash
python tests/run_graph_scenarios.py --count 50 --concurrency 3
```

For a dedicated staging service only:

```bash
python tests/run_graph_scenarios.py --base-url https://YOUR-STAGING-SERVICE --allow-remote --count 20 --concurrency 2
```

Optional AI investigation (may be slow and incur model usage):

```bash
python tests/run_graph_scenarios.py --count 3 --investigate
```

## What the runner does

1. Creates incidents using the real service names.
2. Requests `GET /incidents/{id}/causal-graph` so the backend graph builder runs for that incident.
3. Records graph node/edge counts and labels in the JSON report.
4. By default closes the incident through `/close` with a scenario-specific root cause and resolution.
5. Re-fetches incident detail to verify resolution persistence.

The `events` arrays in `scenarios.json` are a planned event narrative and test fixture, not automatically inserted into operational-event storage. The graph will show configured service dependencies from the backend's dependency catalogue. Event/deployment/error nodes appear only if the backend has an API or fixture loader that persists those events. This avoids pretending that unpersisted synthetic events are real evidence.

## Scenario catalogue

- `AUTH-01`: stale auth-cache key material affects token validation.
- `PAY-01`: payment database connection pool saturation.
- `ORDER-01`: auth degradation propagates to order submission.
- `NOTIFY-01`: notification queue backlog with multiple possible bottlenecks.
- `PAY-02`: stale payment cache compared with payment database state.
- `GRAPH-01`: ambiguous simultaneous signals; must not claim causality based on time alone.

## Safety

Remote targets require `--allow-remote`, and production-like hostnames are refused.
Use local data or a dedicated staging project. Resolutions are simulated narratives;
this runner does not change real infrastructure. Start with count 1, then 6, then 20–50.
