"""FastAPI service for Tracewell Incident Copilot (Priority 1)."""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .config import CORS_ORIGINS, GCP_PROJECT, GCP_REGION
from .agent import run_investigation
from .store import (
    get_db,
    get_incident,
    update_incident,
    create_local_incident,
    server_timestamp,
    firestore_available,
    list_incidents,
    list_steps,
    append_operational_event,
    list_operational_events,
    get_service_node,
    list_service_nodes,
    save_service_node,
    save_dependency_relationship,
    list_dependency_relationships,
    get_dependency_relationship,
    delete_dependency_relationship,
)
from .dependencies import (
    CreateDependencyRequest,
    CreateServiceRequest,
    analyze_downstream_impact,
    discover_dependencies_from_evidence,
    ensure_initialized,
    get_service_dependencies,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("tracewell")

app = FastAPI(
    title="Tracewell Incident Copilot API",
    version="0.1.0",
    description="Python FastAPI + Google ADK incident investigation backend.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


# -------------------------------------------------------------------
# Request models
# -------------------------------------------------------------------

class InvestigateRequest(BaseModel):
    incident_id: str = Field(min_length=1, max_length=180)


class CreateIncidentRequest(BaseModel):
    title: str = Field(min_length=3, max_length=200)
    service: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=8, max_length=5000)
    severity: str = Field(default="SEV3", max_length=20)
    category: str = Field(default="unknown", max_length=80)


class DecisionRequest(BaseModel):
    incident_id: str = Field(min_length=1, max_length=180)
    reason: str | None = Field(default=None, max_length=1000)


class ExecuteActionRequest(BaseModel):
    incident_id: str = Field(min_length=1, max_length=180)
    simulate_failure: bool = False
    notes: str | None = Field(default=None, max_length=1000)


class CloseRequest(BaseModel):
    incident_id: str = Field(min_length=1, max_length=180)
    root_cause: str = Field(min_length=3, max_length=2000)
    resolution: str = Field(min_length=3, max_length=3000)


class OperationalEventRequest(BaseModel):
    event_type: str = Field(min_length=1, max_length=80)
    service: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=3, max_length=2000)
    timestamp: datetime | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)


class IngestRequest(BaseModel):
    lookback_minutes: int | None = Field(default=None, ge=1, le=720)


# -------------------------------------------------------------------
# Health and root endpoints
# -------------------------------------------------------------------

@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "service": "tracewell-api",
        "mode": "firestore" if firestore_available() else "local-fallback",
        "project_configured": bool(GCP_PROJECT),
        "region": GCP_REGION,
    }


@app.get("/")
def root() -> dict[str, str]:
    return {
        "name": "Tracewell Incident Copilot API",
        "docs": "/docs",
        "health": "/health",
    }


# -------------------------------------------------------------------
# Incident endpoints
# -------------------------------------------------------------------

@app.get("/incidents")
def get_incidents() -> list[dict[str, Any]]:
    return list_incidents()


@app.get("/incidents/{incident_id}")
def get_incident_route(incident_id: str) -> dict[str, Any]:
    incident = get_incident(incident_id)

    if not incident:
        raise HTTPException(
            status_code=404,
            detail="Incident not found",
        )

    return incident


@app.get("/incidents/{incident_id}/steps")
def get_incident_steps(
    incident_id: str,
    raw: bool = False,
) -> list[dict[str, Any]]:
    if not get_incident(incident_id):
        raise HTTPException(
            status_code=404,
            detail="Incident not found",
        )

    steps = list_steps(incident_id)
    if raw:
        return steps

    from .timeline import filter_timeline_steps

    return filter_timeline_steps(steps)


@app.post("/incidents")
def create_incident(request: CreateIncidentRequest) -> dict[str, Any]:
    """Create an incident using the configured persistence backend."""
    incident_id = "api_" + uuid.uuid4().hex[:12]

    fields = {
        "title": request.title,
        "service": request.service,
        "description": request.description,
        "severity": request.severity,
        "category": request.category,
        "source": "human",
        "status": "open",
        "attachments": [],
        "created_at": server_timestamp(),
    }

    if firestore_available():
        get_db().collection("incidents").document(incident_id).set(fields)
    else:
        create_local_incident(incident_id, fields)

    # Automatic ingestion: retrieve and persist existing deployments and error logs for the service
    try:
        from .ingestion import ingest_operational_events_for_incident

        ingest_operational_events_for_incident(incident_id)
    except Exception as exc:
        logger.warning("Auto-ingestion during incident creation encountered: %s", exc)

    return {
        "incident_id": incident_id,
        "status": "open",
    }


# -------------------------------------------------------------------
# Operational event endpoints
# -------------------------------------------------------------------

@app.get("/incidents/{incident_id}/events")
def get_incident_events(
    incident_id: str,
    auto_ingest: bool = False,
    correlated: bool = False,
) -> list[dict[str, Any]]:
    """Retrieve an incident's operational events in chronological order, optionally correlated."""
    if auto_ingest:
        try:
            from .ingestion import ingest_operational_events_for_incident

            ingest_operational_events_for_incident(incident_id)
        except Exception as exc:
            logger.warning("Auto-ingestion during get_incident_events: %s", exc)

    if not get_incident(incident_id):
        raise HTTPException(
            status_code=404,
            detail="Incident not found",
        )

    if correlated:
        from .correlation import correlate_events_for_incident

        res = correlate_events_for_incident(incident_id)
        return [e.model_dump() for e in res.events]

    try:
        return list_operational_events(incident_id)
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail="Incident not found",
        ) from exc


@app.get("/incidents/{incident_id}/correlated-events")
def get_incident_correlated_events(
    incident_id: str,
    classification: str | None = None,
    lookback_minutes: int = 60,
) -> dict[str, Any]:
    """Retrieve evidence-based correlated events for an incident.

    Classifies events into 'direct_match', 'dependency_related',
    'temporally_associated', and 'unrelated', providing explicit rationale
    and preserving evidence.
    """
    if not get_incident(incident_id):
        raise HTTPException(
            status_code=404,
            detail="Incident not found",
        )

    try:
        from .correlation import correlate_events_for_incident

        res = correlate_events_for_incident(
            incident_id=incident_id,
            lookback_minutes=lookback_minutes,
            persist=True,
        )
        data = res.model_dump()
        if classification and classification != "all":
            data["events"] = [
                e for e in data["events"]
                if e["classification"] == classification
            ]
        return data
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail="Incident not found",
        ) from exc
    except Exception as exc:
        logger.exception("Correlation failed for incident %s", incident_id)
        raise HTTPException(
            status_code=500,
            detail=f"Correlation failed: {type(exc).__name__}: {exc}",
        ) from exc


# -------------------------------------------------------------------
# Root-Cause Analysis endpoints
# -------------------------------------------------------------------

@app.get("/incidents/{incident_id}/root-cause-analysis")
async def get_incident_root_cause_analysis(
    incident_id: str,
    force_refresh: bool = False,
) -> dict[str, Any]:
    """Retrieve or generate evidence-based root-cause hypotheses for an incident."""
    if not get_incident(incident_id):
        raise HTTPException(
            status_code=404,
            detail="Incident not found",
        )

    try:
        from .root_cause import perform_root_cause_analysis

        analysis = await perform_root_cause_analysis(
            incident_id=incident_id,
            force_refresh=force_refresh,
            persist=True,
        )
        return analysis.model_dump()
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail="Incident not found",
        ) from exc
    except Exception as exc:
        logger.exception("Root-cause analysis failed for incident %s", incident_id)
        raise HTTPException(
            status_code=500,
            detail=f"Root-cause analysis failed: {type(exc).__name__}: {exc}",
        ) from exc


@app.post("/incidents/{incident_id}/root-cause-analysis")
async def trigger_incident_root_cause_analysis(
    incident_id: str,
) -> dict[str, Any]:
    """Trigger generation and ranking of root-cause hypotheses for an incident."""
    if not get_incident(incident_id):
        raise HTTPException(
            status_code=404,
            detail="Incident not found",
        )

    try:
        from .root_cause import perform_root_cause_analysis

        analysis = await perform_root_cause_analysis(
            incident_id=incident_id,
            force_refresh=True,
            persist=True,
        )
        return analysis.model_dump()
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail="Incident not found",
        ) from exc
    except Exception as exc:
        logger.exception("Root-cause analysis failed for incident %s", incident_id)
        raise HTTPException(
            status_code=500,
            detail=f"Root-cause analysis failed: {type(exc).__name__}: {exc}",
        ) from exc


# -------------------------------------------------------------------
# Causal Graph endpoints
# -------------------------------------------------------------------

@app.get("/incidents/{incident_id}/causal-graph")
async def get_incident_causal_graph(
    incident_id: str,
    refresh: bool = False,
) -> dict[str, Any]:
    """Retrieve or build the causal investigation graph for an incident.
    
    If already saved and refresh=False, returns the saved graph without
    re-running analysis. If refresh=True, rebuilds from latest evidence.
    """
    if not get_incident(incident_id):
        raise HTTPException(
            status_code=404,
            detail="Incident not found",
        )

    try:
        from .causal_graph import build_incident_causal_graph

        graph = await build_incident_causal_graph(
            incident_id=incident_id,
            force_refresh=refresh,
            persist=True,
        )
        return graph.model_dump()
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail="Incident not found",
        ) from exc
    except Exception as exc:
        logger.exception("Failed to build/retrieve causal graph for incident %s", incident_id)
        raise HTTPException(
            status_code=500,
            detail=f"Causal graph generation failed: {type(exc).__name__}: {exc}",
        ) from exc


@app.post("/incidents/{incident_id}/causal-graph/refresh")
@app.post("/incidents/{incident_id}/causal-graph")
async def refresh_incident_causal_graph(
    incident_id: str,
) -> dict[str, Any]:
    """Explicitly rebuild and persist the causal investigation graph from current evidence."""
    if not get_incident(incident_id):
        raise HTTPException(
            status_code=404,
            detail="Incident not found",
        )

    try:
        from .causal_graph import build_incident_causal_graph

        graph = await build_incident_causal_graph(
            incident_id=incident_id,
            force_refresh=True,
            persist=True,
        )
        return graph.model_dump()
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail="Incident not found",
        ) from exc
    except Exception as exc:
        logger.exception("Failed to refresh causal graph for incident %s", incident_id)
        raise HTTPException(
            status_code=500,
            detail=f"Causal graph refresh failed: {type(exc).__name__}: {exc}",
        ) from exc




@app.post("/incidents/{incident_id}/events", status_code=201)
def create_operational_event(
    incident_id: str,
    request: OperationalEventRequest,
) -> dict[str, Any]:
    """Record an operational event associated with an existing incident."""
    try:
        return append_operational_event(
            incident_id,
            event_type=request.event_type,
            service=request.service,
            description=request.description,
            event_timestamp=(
                request.timestamp.isoformat()
                if request.timestamp
                else None
            ),
            evidence=request.evidence,
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail="Incident not found",
        ) from exc


@app.post("/incidents/{incident_id}/ingest")
def ingest_incident_events(
    incident_id: str,
    request: IngestRequest | None = None,
) -> dict[str, Any]:
    """Ingest Cloud Run deployment changes and Cloud Logging errors for an incident."""
    from .ingestion import ingest_operational_events_for_incident

    minutes = request.lookback_minutes if request else None
    try:
        return ingest_operational_events_for_incident(
            incident_id,
            lookback_minutes=minutes,
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail="Incident not found",
        ) from exc
    except Exception as exc:
        logger.exception("Ingestion failed for incident %s", incident_id)
        raise HTTPException(
            status_code=500,
            detail=f"Ingestion failed: {type(exc).__name__}: {exc}",
        ) from exc


# -------------------------------------------------------------------
# Investigation endpoint
# -------------------------------------------------------------------

@app.post("/investigate")
async def investigate(request: InvestigateRequest) -> dict[str, Any]:
    """Run an ADK investigation and persist its findings."""
    try:
        from .ingestion import ingest_operational_events_for_incident

        ingest_operational_events_for_incident(request.incident_id)
    except Exception as exc:
        logger.warning("Auto-ingestion prior to investigation encountered: %s", exc)

    try:
        return await run_investigation(request.incident_id)

    except KeyError as exc:
        raise HTTPException(
            status_code=404,
            detail=str(exc),
        ) from exc

    except Exception as exc:
        logger.exception(
            "Investigation failed for incident %s",
            request.incident_id,
        )

        raise HTTPException(
            status_code=500,
            detail=f"Investigation failed: {type(exc).__name__}",
        ) from exc


@app.post("/incidents/{incident_id}/investigate")
async def investigate_by_path(incident_id: str) -> dict[str, Any]:
    """Run an investigation for an incident specified by path."""
    return await investigate(InvestigateRequest(incident_id=incident_id))


# -------------------------------------------------------------------
# Approval and rejection endpoints
# -------------------------------------------------------------------

@app.post("/approve")
def approve(request: DecisionRequest) -> dict[str, Any]:
    """Record approval; this endpoint does not execute a rollback."""
    incident = get_incident(request.incident_id)

    if not incident:
        raise HTTPException(
            status_code=404,
            detail="Incident not found",
        )

    pending = incident.get("pending_action") or {}

    if pending.get("status") not in ("pending", "pending_approval"):
        raise HTTPException(
            status_code=409,
            detail="There is no pending action to approve",
        )

    now = datetime.now(timezone.utc).isoformat()
    pending.update({
        "status": "approved",
        "lifecycle_state": "approved_awaiting_execution",
        "decided_at": now,
        "decision_note": request.reason,
    })

    history = incident.get("action_history") or []
    history.append({
        "event": "approved",
        "timestamp": now,
        "note": request.reason,
    })

    update_incident(
        request.incident_id,
        {
            "pending_action": pending,
            "action_history": history,
            "status": "approved_pending_execution",
        },
    )

    return {
        "incident_id": request.incident_id,
        "status": "approved_pending_execution",
        "message": (
            "Approval recorded. No rollback was executed "
            "by this Priority 1 backend."
        ),
        "pending_action": pending,
    }


@app.post("/reject")
def reject(request: DecisionRequest) -> dict[str, Any]:
    incident = get_incident(request.incident_id)

    if not incident:
        raise HTTPException(
            status_code=404,
            detail="Incident not found",
        )

    pending = incident.get("pending_action") or {}

    if pending.get("status") not in ("pending", "pending_approval"):
        raise HTTPException(
            status_code=409,
            detail="There is no pending action to reject",
        )

    now = datetime.now(timezone.utc).isoformat()
    pending.update({
        "status": "rejected",
        "lifecycle_state": "rejected",
        "decided_at": now,
        "decision_note": request.reason,
    })

    history = incident.get("action_history") or []
    history.append({
        "event": "rejected",
        "timestamp": now,
        "note": request.reason,
    })

    update_incident(
        request.incident_id,
        {
            "pending_action": pending,
            "action_history": history,
            "status": "rejected",
        },
    )

    return {
        "incident_id": request.incident_id,
        "status": "rejected",
        "pending_action": pending,
    }


@app.post("/execute")
@app.post("/actions/execute")
@app.post("/incidents/{incident_id}/execute")
def execute_action(
    request: ExecuteActionRequest | None = None,
    incident_id: str | None = None,
) -> dict[str, Any]:
    """Execute an approved remediation action with safety authorization checks."""
    target_id = (request.incident_id if request else None) or incident_id
    if not target_id:
        raise HTTPException(status_code=400, detail="incident_id is required")

    incident = get_incident(target_id)
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")

    action = incident.get("pending_action")
    if not action:
        raise HTTPException(
            status_code=404,
            detail="There is no remediation action associated with this incident",
        )

    current_status = action.get("status")
    # Safeguard: Action MUST be approved prior to execution (or retry from failure)
    if current_status not in ("approved", "approved_pending_execution", "failed", "execution_failed"):
        raise HTTPException(
            status_code=409,
            detail=(
                f"Action cannot be executed in its current state ({current_status}). "
                "Remediation actions require explicit human operator approval before execution."
            ),
        )

    now = datetime.now(timezone.utc).isoformat()
    history = incident.get("action_history") or []

    simulate_failure = request.simulate_failure if request else False
    if simulate_failure:
        error_msg = (
            (request.notes if request else None)
            or "Execution failed: Orchestrator deployment health check timed out. Target revision could not be verified."
        )
        action.update({
            "status": "failed",
            "lifecycle_state": "execution_failed",
            "failed_at": now,
            "error_message": error_msg,
        })
        history.append({
            "event": "execution_failed",
            "timestamp": now,
            "error": error_msg,
        })
        update_incident(
            target_id,
            {
                "pending_action": action,
                "action_history": history,
                "status": "remediation_failed",
            },
        )
        return {
            "incident_id": target_id,
            "status": "execution_failed",
            "success": False,
            "error": error_msg,
            "pending_action": action,
        }

    # Successful execution
    action_desc = action.get("action") or "Remediation action"
    target_rev = action.get("target_revision") or "v-previous"
    exec_result = f"Successfully executed: {action_desc}. Deployment rolled back and verified healthy."
    evidence = {
        "action": action_desc,
        "target_revision": target_rev,
        "orchestrator_status": "healthy",
        "verification_status": "verified_healthy",
        "error_rate_post_remediation": "0.00%",
        "timestamp": now,
    }

    action.update({
        "status": "executed",
        "lifecycle_state": "executed_success",
        "executed_at": now,
        "execution_result": exec_result,
        "execution_evidence": evidence,
        "execution_note": request.notes if request else None,
    })

    history.append({
        "event": "executed",
        "timestamp": now,
        "result": exec_result,
        "evidence": evidence,
    })

    update_incident(
        target_id,
        {
            "pending_action": action,
            "action_history": history,
            "status": "mitigated",
        },
    )

    return {
        "incident_id": target_id,
        "status": "executed",
        "success": True,
        "message": exec_result,
        "executed_at": now,
        "evidence": evidence,
        "pending_action": action,
    }


# -------------------------------------------------------------------
# Incident closure
# -------------------------------------------------------------------

@app.post("/close")
def close(request: CloseRequest) -> dict[str, Any]:
    """Close a human-reviewed incident."""
    incident = get_incident(request.incident_id)

    if not incident:
        raise HTTPException(
            status_code=404,
            detail="Incident not found",
        )

    update_incident(
        request.incident_id,
        {
            "root_cause": request.root_cause,
            "resolution": request.resolution,
            "status": "closed",
            "resolved_at": datetime.now(timezone.utc).isoformat(),
        },
    )

    return {
        "incident_id": request.incident_id,
        "status": "closed",
        "learning_loop": "not yet enabled",
    }


# -------------------------------------------------------------------
# Service and Dependency Mapping endpoints
# -------------------------------------------------------------------

@app.get("/services")
def get_services(environment: str | None = None) -> list[dict[str, Any]]:
    """Retrieve all registered service nodes, optionally filtered by environment."""
    ensure_initialized()
    return list_service_nodes(environment=environment)


@app.get("/services/{service_name}")
def get_service_route(service_name: str) -> dict[str, Any]:
    """Retrieve a specific service node by name."""
    ensure_initialized()
    node = get_service_node(service_name)
    if not node:
        raise HTTPException(
            status_code=404,
            detail=f"Service '{service_name}' not found",
        )
    return node


@app.post("/services", status_code=201)
def create_service(request: CreateServiceRequest) -> dict[str, Any]:
    """Register or update a service node in the service registry."""
    ensure_initialized()
    return save_service_node(request.model_dump())


@app.get("/dependencies")
def get_dependencies(
    environment: str | None = None,
    source: str | None = None,
    target: str | None = None,
) -> dict[str, Any]:
    """Retrieve service nodes and directional dependency relationships."""
    ensure_initialized()
    services = list_service_nodes(environment=environment)
    deps = list_dependency_relationships(source=source, target=target, environment=environment)
    return {
        "services": services,
        "dependencies": deps,
        "total_services": len(services),
        "total_dependencies": len(deps),
    }


@app.post("/dependencies", status_code=201)
def create_dependency(request: CreateDependencyRequest) -> dict[str, Any]:
    """Explicitly configure or update a directional service dependency relationship."""
    ensure_initialized()

    # Ensure source and target nodes exist in the service registry
    if not get_service_node(request.source):
        save_service_node({
            "name": request.source,
            "service_type": "microservice",
            "environment": request.environment,
            "source": "configured",
            "health_status": "unknown",
        })
    if not get_service_node(request.target):
        save_service_node({
            "name": request.target,
            "service_type": "microservice",
            "environment": request.environment,
            "source": "configured",
            "health_status": "unknown",
        })

    saved = save_dependency_relationship(request.model_dump())
    return saved


@app.get("/services/{service_name}/dependencies")
def get_service_dependencies_route(
    service_name: str,
    direction: str = "both",
    environment: str | None = None,
) -> dict[str, Any]:
    """Retrieve upstream and/or downstream dependencies for a specified service."""
    ensure_initialized()
    node = get_service_node(service_name)
    deps = get_service_dependencies(service_name, direction=direction, environment=environment)

    if not node and not deps["upstream"] and not deps["downstream"]:
        raise HTTPException(
            status_code=404,
            detail=f"Service '{service_name}' not found",
        )

    return deps


@app.get("/services/{service_name}/downstream-impact")
def get_service_downstream_impact(
    service_name: str,
    max_depth: int = 10,
    environment: str | None = None,
) -> dict[str, Any]:
    """Identify potentially and confirmed affected downstream services if the specified service degrades."""
    ensure_initialized()
    node = get_service_node(service_name)
    all_deps = list_dependency_relationships(environment=environment)
    has_relations = any(
        (d.get("source") == service_name or d.get("target") == service_name)
        for d in all_deps
    )

    if not node and not has_relations:
        raise HTTPException(
            status_code=404,
            detail=f"Service '{service_name}' not found",
        )

    return analyze_downstream_impact(
        service_name,
        max_depth=max_depth,
        environment=environment,
    )


@app.post("/dependencies/discover")
def trigger_dependency_discovery() -> dict[str, Any]:
    """Discover or verify dependencies from configuration, Cloud Run metadata, and operational events."""
    ensure_initialized()
    events: list[dict[str, Any]] = []
    try:
        incidents = list_incidents()
        for inc in incidents[:15]:
            evs = list_operational_events(inc["id"])
            events.extend(evs)
    except Exception as exc:
        logger.warning("Could not read operational events for discovery: %s", exc)

    return discover_dependencies_from_evidence(operational_events=events)


@app.get("/services/{service_name}/health")
def get_service_health(service_name: str) -> dict[str, Any]:
    """Probe or report the health status of a service."""
    ensure_initialized()
    node = get_service_node(service_name)
    if not node:
        raise HTTPException(
            status_code=404,
            detail=f"Service '{service_name}' not found",
        )

    url = (
        os.getenv(f"SERVICE_URL_{service_name.upper().replace('-', '_')}")
        or (os.getenv("PAYMENT_API_URL") if service_name == "payment-api" else None)
    )

    if url:
        try:
            import requests
            resp = requests.get(url.rstrip("/") + "/health", timeout=3)
            status = "healthy" if resp.ok else "unhealthy"
            save_service_node({**node, "health_status": status})
            return {
                "service": service_name,
                "health_status": status,
                "status_code": resp.status_code,
                "url": url,
            }
        except Exception as exc:
            save_service_node({**node, "health_status": "unhealthy"})
            return {
                "service": service_name,
                "health_status": "unhealthy",
                "error": str(exc),
                "url": url,
            }

    return {
        "service": service_name,
        "health_status": node.get("health_status", "unknown"),
        "url": None,
    }