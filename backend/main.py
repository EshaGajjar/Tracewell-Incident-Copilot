"""FastAPI service for Tracewell Incident Copilot (Priority 1)."""
from __future__ import annotations
import logging
from datetime import datetime, timezone
from typing import Any
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from .config import CORS_ORIGINS, GCP_PROJECT, GCP_REGION
from .agent import run_investigation
from .store import (get_db, get_incident, update_incident, create_local_incident, server_timestamp, firestore_available, list_incidents, list_steps)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("tracewell")
app = FastAPI(title="Tracewell Incident Copilot API", version="0.1.0",
              description="Python FastAPI + Google ADK incident investigation backend.")
app.add_middleware(CORSMiddleware, allow_origins=CORS_ORIGINS, allow_credentials=False,
                   allow_methods=["GET", "POST", "OPTIONS"], allow_headers=["*"])

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

class CloseRequest(BaseModel):
    incident_id: str = Field(min_length=1, max_length=180)
    root_cause: str = Field(min_length=3, max_length=2000)
    resolution: str = Field(min_length=3, max_length=3000)

@app.get("/health")
def health() -> dict[str, Any]:
    return {"status":"ok", "service":"tracewell-api", "mode":"firestore" if firestore_available() else "local-fallback",
            "project_configured":bool(GCP_PROJECT), "region":GCP_REGION}

@app.get("/")
def root() -> dict[str, str]:
    return {"name":"Tracewell Incident Copilot API", "docs":"/docs", "health":"/health"}

@app.get("/incidents")
def get_incidents() -> list[dict[str, Any]]:
    return list_incidents()

@app.get("/incidents/{incident_id}")
def get_incident_route(incident_id: str) -> dict[str, Any]:
    incident = get_incident(incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")
    return incident

@app.get("/incidents/{incident_id}/steps")
def get_incident_steps(incident_id: str) -> list[dict[str, Any]]:
    if not get_incident(incident_id):
        raise HTTPException(status_code=404, detail="Incident not found")
    return list_steps(incident_id)

@app.post("/incidents")
def create_incident(request: CreateIncidentRequest) -> dict[str, Any]:
    """Create an incident through the same store used by the investigation API."""
    import uuid
    incident_id = "api_" + uuid.uuid4().hex[:12]
    fields = {"title":request.title, "service":request.service, "description":request.description,
              "severity":request.severity, "category":request.category, "source":"human", "status":"open",
              "attachments":[], "created_at":server_timestamp()}
    if firestore_available():
        get_db().collection("incidents").document(incident_id).set(fields)
    else:
        create_local_incident(incident_id, fields)
    return {"incident_id":incident_id, "status":"open"}

@app.post("/investigate")
async def investigate(request: InvestigateRequest) -> dict[str, Any]:
    """Run ADK investigation and persist tool calls, results, diagnosis and pending proposal."""
    try:
        return await run_investigation(request.incident_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Investigation failed for incident %s", request.incident_id)
        raise HTTPException(status_code=500, detail=f"Investigation failed: {type(exc).__name__}") from exc

@app.post("/approve")
def approve(request: DecisionRequest) -> dict[str, Any]:
    """Record approval only. Actual rollback is implemented in Priority 2 and is not executed here."""
    incident = get_incident(request.incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")
    pending = incident.get("pending_action") or {}
    if pending.get("status") != "pending":
        raise HTTPException(status_code=409, detail="There is no pending action to approve")
    pending.update({"status":"approved", "decided_at":datetime.now(timezone.utc).isoformat(), "decision_note":request.reason})
    update_incident(request.incident_id, {"pending_action":pending, "status":"approved_pending_execution"})
    return {"incident_id":request.incident_id, "status":"approved_pending_execution",
            "message":"Approval recorded. No rollback was executed by this Priority 1 backend."}

@app.post("/reject")
def reject(request: DecisionRequest) -> dict[str, Any]:
    incident = get_incident(request.incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")
    pending = incident.get("pending_action") or {}
    if pending.get("status") != "pending":
        raise HTTPException(status_code=409, detail="There is no pending action to reject")
    pending.update({"status":"rejected", "decided_at":datetime.now(timezone.utc).isoformat(), "decision_note":request.reason})
    update_incident(request.incident_id, {"pending_action":pending, "status":"rejected"})
    return {"incident_id":request.incident_id, "status":"rejected"}

@app.post("/close")
def close(request: CloseRequest) -> dict[str, Any]:
    """Close a human-reviewed ticket. Embedding into past_incidents is handled in the learning-loop milestone."""
    incident = get_incident(request.incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")
    update_incident(request.incident_id, {"root_cause":request.root_cause, "resolution":request.resolution,
                                          "status":"closed", "resolved_at":datetime.now(timezone.utc).isoformat()})
    return {"incident_id":request.incident_id, "status":"closed", "learning_loop":"not yet enabled"}
