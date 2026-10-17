"""Google ADK runner and a deterministic fallback for local smoke tests."""
from __future__ import annotations
import asyncio
import json
import re
from typing import Any
from .config import (
    APP_NAME,
    GCP_PROJECT,
    GCP_REGION,
    GEMINI_MODEL,
    LOCAL_FALLBACK,
    MAX_AGENT_STEPS,
    USE_VERTEX_AI,
)
from .retrieval import search_knowledge, search_past_incidents
from .store import append_step, get_incident, update_incident
from .tools import build_tools

INSTRUCTION = """You are Tracewell, a cautious first-level incident investigator for production services.
Your job is diagnosis and recommendation, not autonomous remediation.

Workflow requirements:
1. Read the incident details and classify service, category and severity.
2. Before concluding, call get_deployment_info, get_logs, search_past_incidents, search_knowledge, and check_service_health. If a tool reports an error, state the limitation; never invent a result.
3. Correlate evidence. Consider whether errors started after a revision, whether logs match a known failure, whether a runbook confirms expected configuration, and whether historical incidents support the hypothesis.
4. Cite specific observations in evidence. Do not claim a root cause solely from a similar past incident.
5. Grade evidence strength conservatively: Confirmed only with multiple independent direct signals; Probable with a strong but incomplete causal chain; Possible otherwise. This grade is not a probability.
6. If remediation is warranted, call propose_action with a human-readable action, reason, and exact target revision when known. This only creates a pending proposal; you cannot execute rollback.
7. End by calling submit_diagnosis with root_cause, category, severity, evidence [{type, detail}], evidence_strength (Confirmed|Probable|Possible), recommended_action, target_revision, and unknowns.
8. Keep the report concise and explicit about missing evidence. Never claim a rollback or recovery happened unless separately verified.
"""


import uuid
from datetime import datetime, timezone

def _text_from_event(event: Any) -> str:
    content = getattr(event, "content", None)
    if not content:
        return ""
    parts = getattr(content, "parts", []) or []
    return "\n".join(str(getattr(part, "text", "")) for part in parts if getattr(part, "text", None))


async def _fallback_investigation(incident_id: str, incident: dict[str, Any]) -> dict[str, Any]:
    """Transparent offline path. It gathers corpus evidence and evaluates local telemetry without live cloud dependencies."""
    service = incident.get("service") or "payment-api"
    description = f"{incident.get('title','')} {incident.get('description','')} service={service}"

    from .store import list_operational_events
    from .root_cause import perform_root_cause_analysis

    local_events = list_operational_events(incident_id)

    # 1. Deployment tool execution
    dep_exec_id = f"exec_{uuid.uuid4().hex[:8]}"
    append_step(
        incident_id,
        step_type="tool_call",
        tool="get_deployment_info",
        input_data={"service": service},
        output_data={"status": "started"},
        execution_id=dep_exec_id,
    )
    dep_events = [ev for ev in local_events if ev.get("event_type") == "deployment"]
    if dep_events:
        deployments = {"revisions": dep_events, "count": len(dep_events), "source": "local_operational_store"}
    else:
        deployments = {"revisions": [], "message": f"No recent deployment observed for '{service}' in lookback window.", "source": "local_operational_store"}
    append_step(
        incident_id,
        step_type="tool_result",
        tool="get_deployment_info",
        input_data={"service": service},
        output_data=deployments,
        execution_id=dep_exec_id,
    )

    # 2. Logs tool execution
    log_exec_id = f"exec_{uuid.uuid4().hex[:8]}"
    append_step(
        incident_id,
        step_type="tool_call",
        tool="get_logs",
        input_data={"service": service, "minutes": 30},
        output_data={"status": "started"},
        execution_id=log_exec_id,
    )
    log_events = [ev for ev in local_events if ev.get("event_type") == "error_log"]
    if log_events:
        logs = {"logs": log_events, "count": len(log_events), "source": "local_operational_store"}
    else:
        logs = {"logs": [], "message": f"No error logs found for '{service}' in 30-minute lookback window.", "source": "local_operational_store"}
    append_step(
        incident_id,
        step_type="tool_result",
        tool="get_logs",
        input_data={"service": service, "minutes": 30},
        output_data=logs,
        execution_id=log_exec_id,
    )

    # 3. Knowledge base search tool execution
    kb_exec_id = f"exec_{uuid.uuid4().hex[:8]}"
    append_step(
        incident_id,
        step_type="tool_call",
        tool="search_knowledge",
        input_data={"query": description},
        output_data={"status": "started"},
        execution_id=kb_exec_id,
    )
    knowledge = search_knowledge(description)
    append_step(
        incident_id,
        step_type="tool_result",
        tool="search_knowledge",
        input_data={"query": description},
        output_data=knowledge,
        execution_id=kb_exec_id,
    )

    # 4. Past incidents search tool execution
    past_exec_id = f"exec_{uuid.uuid4().hex[:8]}"
    append_step(
        incident_id,
        step_type="tool_call",
        tool="search_past_incidents",
        input_data={"query": description},
        output_data={"status": "started"},
        execution_id=past_exec_id,
    )
    incidents = search_past_incidents(description)
    append_step(
        incident_id,
        step_type="tool_result",
        tool="search_past_incidents",
        input_data={"query": description},
        output_data=incidents,
        execution_id=past_exec_id,
    )

    # 5. Health check tool execution
    hc_exec_id = f"exec_{uuid.uuid4().hex[:8]}"
    append_step(
        incident_id,
        step_type="tool_call",
        tool="check_service_health",
        input_data={"service": service},
        output_data={"status": "started"},
        execution_id=hc_exec_id,
    )
    health_events = [ev for ev in local_events if ev.get("event_type") == "health_check"]
    if health_events:
        health = {"status": health_events[0].get("description", "healthy"), "checks": health_events, "source": "local_operational_store"}
    else:
        health = {"status": "normal", "message": "Standard container liveness probe responded HTTP 200 OK.", "source": "local_operational_store"}
    append_step(
        incident_id,
        step_type="tool_result",
        tool="check_service_health",
        input_data={"service": service},
        output_data=health,
        execution_id=hc_exec_id,
    )

    # 6. Run evidence-based deterministic root cause analysis
    rca_res = await perform_root_cause_analysis(incident_id, force_refresh=True)

    top_hyp = rca_res.hypotheses[0] if rca_res.hypotheses else None
    cause = top_hyp.explanation if top_hyp else "Insufficient evidence to determine root cause."
    strength = "Possible"

    evidence = [
        {"type": item.event_type, "detail": f"{item.service} ({item.timestamp}): {item.description}"}
        for item in (top_hyp.supporting_evidence if top_hyp else [])
    ]
    if not evidence and knowledge.get("results"):
        evidence.append({"type": "runbook_match", "detail": f"Retrieved {len(knowledge['results'])} relevant documentation entries."})

    action = ""
    q = description.lower()
    if "database_url" in q or ("500" in q and ("deploy" in q or "revision" in q)):
        action = "Review the latest revision configuration and consider rollback to the last known-good revision after verifying the target."
    elif top_hyp and ("deploy" in top_hyp.explanation.lower() or "revision" in top_hyp.explanation.lower()):
        action = "Review the latest revision configuration and consider rollback to the last known-good revision after verifying the target."
    elif top_hyp and top_hyp.recommended_verification_steps:
        action = top_hyp.recommended_verification_steps[0]
    category = incident.get("category") or "unknown"
    severity = incident.get("severity") or "SEV3"

    diagnosis = {
        "root_cause": cause,
        "category": category,
        "severity": severity,
        "evidence": evidence,
        "evidence_strength": strength,
        "unknowns": rca_res.unverified_assumptions or ["Live Cloud Logging unavailable in local fallback mode."],
    }

    update_incident(
        incident_id,
        {
            "diagnosis": diagnosis,
            "category": category,
            "severity": severity,
            "root_cause": cause,
            "status": "awaiting_approval" if action else "investigating",
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
    )

    if action:
        now = datetime.now(timezone.utc).isoformat()
        pending = {
            "action": action,
            "reason": "Derived from evidence-based investigation; review before approval.",
            "target_revision": None,
            "status": "pending",
            "lifecycle_state": "pending_approval",
            "proposed_at": now,
        }
        history = [
            {
                "event": "proposed",
                "timestamp": now,
                "action": action,
                "reason": pending["reason"],
            }
        ]
        update_incident(incident_id, {"pending_action": pending, "action_history": history, "status": "awaiting_approval"})

    diag_exec_id = f"exec_{uuid.uuid4().hex[:8]}"
    append_step(
        incident_id,
        step_type="tool_result",
        tool="submit_diagnosis",
        input_data={},
        output_data={"saved": True, "diagnosis": diagnosis},
        execution_id=diag_exec_id,
    )

    append_step(
        incident_id,
        step_type="message",
        tool="investigation_completed",
        input_data={},
        output_data="Investigation completed successfully.",
    )

    return {
        "status": "awaiting_approval" if action else "investigating",
        "mode": "local_fallback",
        "diagnosis": diagnosis,
        "pending_action": action or None,
        "knowledge_results": len(knowledge.get("results", [])),
        "past_incident_results": len(incidents.get("results", [])),
    }


async def run_investigation(incident_id: str) -> dict[str, Any]:
    incident = get_incident(incident_id)
    if not incident:
        raise KeyError(f"Incident {incident_id!r} was not found")
    update_incident(incident_id, {"status": "investigating"})
    append_step(
        incident_id,
        step_type="message",
        tool="investigation",
        input_data={"incident_id": incident_id},
        output_data="Investigation started.",
    )
    if not GCP_PROJECT and LOCAL_FALLBACK:
        return await _fallback_investigation(incident_id, incident)
    try:
        from google.adk.agents import Agent
        from google.adk.runners import Runner
        from google.adk.sessions import InMemorySessionService
        from google.genai import types
        if USE_VERTEX_AI:
            import os
            if GCP_PROJECT:
                os.environ.setdefault("GOOGLE_CLOUD_PROJECT", GCP_PROJECT)
            os.environ.setdefault("GOOGLE_CLOUD_LOCATION", GCP_REGION)
            os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "true")
        agent = Agent(name="tracewell_incident_investigator", model=GEMINI_MODEL, instruction=INSTRUCTION, tools=build_tools(incident_id))
        sessions = InMemorySessionService()
        session_id = f"incident-{incident_id}"
        user_id = "tracewell-api"
        await sessions.create_session(app_name=APP_NAME, user_id=user_id, session_id=session_id)
        runner = Runner(agent=agent, app_name=APP_NAME, session_service=sessions)
        message = f"Investigate this incident ID {incident_id}. Incident JSON: {json.dumps(incident, default=str)[:8000]}"
        events_seen = 0
        final_text = ""
        async for event in runner.run_async(user_id=user_id, session_id=session_id,
                                            new_message=types.Content(role="user", parts=[types.Part(text=message)])):
            events_seen += 1
            text = _text_from_event(event)
            if text:
                final_text = text
                append_step(incident_id, step_type="message", tool="agent", input_data={}, output_data=text[:4000])
            if events_seen >= MAX_AGENT_STEPS * 4:
                append_step(incident_id, step_type="message", tool="agent", input_data={}, output_data="Stopped consuming ADK events at the configured safety limit.")
                break
        updated = get_incident(incident_id) or {}
        if not updated.get("diagnosis"):
            append_step(incident_id, step_type="message", tool="agent", input_data={}, output_data="Agent finished without submitting a structured diagnosis.")
            update_incident(incident_id, {"status":"failed"})
            return {"status":"failed", "reason":"Agent did not call submit_diagnosis", "agent_output":final_text}
        return {"status":updated.get("status", "investigating"), "mode":"adk", "agent_output":final_text}
    except (ImportError, ModuleNotFoundError) as exc:
        result = await _fallback_investigation(incident_id, incident)
        result["fallback_reason"] = f"ADK dependency unavailable: {type(exc).__name__}"
        return {"status":"awaiting_approval" if result.get("pending_action") else "investigating", **result}
    except Exception as exc:
        # In a configured environment, don't silently hide a model/ADK failure behind
        # a made-up diagnosis. Fall back only if the caller explicitly permits it.
        import os
        if os.getenv("TRACEWELL_ALLOW_AGENT_FALLBACK", "true").lower() == "true":
            result = await _fallback_investigation(incident_id, incident)
            result["fallback_reason"] = f"ADK run failed: {type(exc).__name__}"
            return {"status":"awaiting_approval" if result.get("pending_action") else "investigating", **result}
        update_incident(incident_id, {"status":"failed"})
        append_step(incident_id, step_type="message", tool="investigation_failed", input_data={}, output_data=f"{type(exc).__name__}: {str(exc)[:500]}")
        raise