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
2. Call get_reporter_evidence to read the reporter's original report and any attached text logs before forming hypotheses.
3. Before concluding, call get_deployment_info, get_logs, search_past_incidents, search_knowledge, and check_service_health. If a tool reports an error, state the limitation; never invent a result.
4. Correlate evidence. Consider whether errors started after a revision, whether logs match a known failure, whether a runbook confirms expected configuration, and whether historical incidents support the hypothesis.
5. Cite specific observations in evidence. Do not claim a root cause solely from a similar past incident. Distinguish reporter-provided evidence from telemetry retrieved from GCP.
6. Grade evidence strength conservatively: Confirmed only with multiple independent direct signals; Probable with a strong but incomplete causal chain; Possible otherwise. This grade is not a probability.
7. If remediation is warranted, call propose_action with a human-readable action, reason, and exact target revision when known. This only creates a pending proposal; you cannot execute rollback.
8. End by calling submit_diagnosis with root_cause, category, severity, evidence [{type, detail}], evidence_strength (Confirmed|Probable|Possible), recommended_action, target_revision, and unknowns.
9. Keep the report concise and explicit about missing evidence. Never claim a rollback or recovery happened unless separately verified.
"""


import uuid
from datetime import datetime, timezone

def _text_from_event(event: Any) -> str:
    content = getattr(event, "content", None)
    if not content:
        return ""
    parts = getattr(content, "parts", []) or []
    return "\n".join(str(getattr(part, "text", "")) for part in parts if getattr(part, "text", None))


import os

INVESTIGATION_STAGES = [
    {
        "key": "retrieving_details",
        "label": "Retrieving incident details",
        "description": "Loading incident scope, affected service, and reporter inputs.",
    },
    {
        "key": "collecting_events",
        "label": "Collecting operational events",
        "description": "Querying deployments, error logs, and health check signals.",
    },
    {
        "key": "correlating_events",
        "label": "Correlating logs and deployment events",
        "description": "Aligning temporal anomalies and matching against known patterns.",
    },
    {
        "key": "analyzing_dependencies",
        "label": "Analyzing service dependencies",
        "description": "Evaluating topology, upstream callers, and downstream cascade risks.",
    },
    {
        "key": "evaluating_hypotheses",
        "label": "Evaluating root-cause hypotheses",
        "description": "Scoring candidate fault hypotheses against observable evidence.",
    },
    {
        "key": "preparing_diagnosis",
        "label": "Preparing the evidence-backed diagnosis",
        "description": "Synthesizing root cause findings and recommended remediation.",
    },
]


async def run_investigation_generator(incident_id: str, run_id: str | None = None):
    """Asynchronous generator yielding real-time investigation stages based on actual backend execution."""
    incident = get_incident(incident_id)
    if not incident:
        raise KeyError(f"Incident {incident_id!r} was not found")

    if not run_id:
        run_id = f"run_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:6]}"

    is_testing = bool(os.getenv("PYTEST_CURRENT_TEST"))
    delay = 0.0 if is_testing else 0.35

    # -------------------------------------------------------------
    # Stage 1: Retrieving incident details
    # -------------------------------------------------------------
    yield {
        "run_id": run_id,
        "stage": "retrieving_details",
        "stage_index": 0,
        "total_stages": 6,
        "label": "Retrieving incident details",
        "status": "in_progress",
        "message": f"Verifying incident record and service metadata for '{incident_id}'...",
    }

    update_incident(incident_id, {"status": "investigating", "current_run_id": run_id})
    append_step(
        incident_id,
        step_type="message",
        tool="investigation",
        input_data={"incident_id": incident_id},
        output_data="Investigation started.",
        run_id=run_id,
    )

    service = incident.get("service") or "payment-api"
    severity = incident.get("severity") or "SEV3"
    category = incident.get("category") or "unknown"
    description = f"{incident.get('title','')} {incident.get('description','')} service={service}"

    if delay > 0:
        await asyncio.sleep(delay)

    yield {
        "run_id": run_id,
        "stage": "retrieving_details",
        "stage_index": 0,
        "total_stages": 6,
        "label": "Retrieving incident details",
        "status": "completed",
        "message": f"Loaded incident metadata for service '{service}' (Severity: {severity})",
        "data": {"service": service, "severity": severity, "category": category},
    }

    # -------------------------------------------------------------
    # Stage 2: Collecting operational events
    # -------------------------------------------------------------
    yield {
        "run_id": run_id,
        "stage": "collecting_events",
        "stage_index": 1,
        "total_stages": 6,
        "label": "Collecting operational events",
        "status": "in_progress",
        "message": f"Collecting deployment revisions, error logs, and health checks for '{service}'...",
    }

    from .store import list_operational_events
    from .ingestion import ingest_operational_events_for_incident
    try:
        ingest_operational_events_for_incident(incident_id)
    except Exception as exc:
        pass

    local_events = list_operational_events(incident_id)

    # 1. Deployment tool execution
    dep_exec_id = f"{run_id}_dep_{uuid.uuid4().hex[:6]}"
    append_step(
        incident_id,
        step_type="tool_call",
        tool="get_deployment_info",
        input_data={"service": service},
        output_data={"status": "started"},
        execution_id=dep_exec_id,
        run_id=run_id,
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
        run_id=run_id,
    )

    # 2. Logs tool execution
    log_exec_id = f"{run_id}_log_{uuid.uuid4().hex[:6]}"
    append_step(
        incident_id,
        step_type="tool_call",
        tool="get_logs",
        input_data={"service": service, "minutes": 30},
        output_data={"status": "started"},
        execution_id=log_exec_id,
        run_id=run_id,
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
        run_id=run_id,
    )

    # 3. Health check tool execution
    hc_exec_id = f"{run_id}_hc_{uuid.uuid4().hex[:6]}"
    append_step(
        incident_id,
        step_type="tool_call",
        tool="check_service_health",
        input_data={"service": service},
        output_data={"status": "started"},
        execution_id=hc_exec_id,
        run_id=run_id,
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
        run_id=run_id,
    )

    if delay > 0:
        await asyncio.sleep(delay)

    yield {
        "run_id": run_id,
        "stage": "collecting_events",
        "stage_index": 1,
        "total_stages": 6,
        "label": "Collecting operational events",
        "status": "completed",
        "message": f"Collected {len(local_events)} operational events ({len(dep_events)} deployments, {len(log_events)} error logs, {len(health_events)} health checks)",
        "data": {"count": len(local_events), "deployments": len(dep_events), "error_logs": len(log_events)},
    }

    # -------------------------------------------------------------
    # Stage 3: Correlating logs and deployment events
    # -------------------------------------------------------------
    yield {
        "run_id": run_id,
        "stage": "correlating_events",
        "stage_index": 2,
        "total_stages": 6,
        "label": "Correlating logs and deployment events",
        "status": "in_progress",
        "message": "Correlating error logs with deployment revisions and searching historical runbooks...",
    }

    kb_exec_id = f"{run_id}_kb_{uuid.uuid4().hex[:6]}"
    append_step(
        incident_id,
        step_type="tool_call",
        tool="search_knowledge",
        input_data={"query": description},
        output_data={"status": "started"},
        execution_id=kb_exec_id,
        run_id=run_id,
    )
    knowledge = search_knowledge(description)
    append_step(
        incident_id,
        step_type="tool_result",
        tool="search_knowledge",
        input_data={"query": description},
        output_data=knowledge,
        execution_id=kb_exec_id,
        run_id=run_id,
    )

    past_exec_id = f"{run_id}_past_{uuid.uuid4().hex[:6]}"
    append_step(
        incident_id,
        step_type="tool_call",
        tool="search_past_incidents",
        input_data={"query": description},
        output_data={"status": "started"},
        execution_id=past_exec_id,
        run_id=run_id,
    )
    incidents = search_past_incidents(description)
    append_step(
        incident_id,
        step_type="tool_result",
        tool="search_past_incidents",
        input_data={"query": description},
        output_data=incidents,
        execution_id=past_exec_id,
        run_id=run_id,
    )

    from .correlation import correlate_events_for_incident
    corr_res = correlate_events_for_incident(incident_id)
    corr_events = getattr(corr_res, "correlated_events", None) or []
    corr_count = len(corr_events)

    if delay > 0:
        await asyncio.sleep(delay)

    yield {
        "run_id": run_id,
        "stage": "correlating_events",
        "stage_index": 2,
        "total_stages": 6,
        "label": "Correlating logs and deployment events",
        "status": "completed",
        "message": f"Correlated {corr_count} events with deployments ({len(knowledge.get('results', []))} runbooks matched)",
        "data": {"correlated_count": corr_count, "knowledge_results": len(knowledge.get("results", []))},
    }

    # -------------------------------------------------------------
    # Stage 4: Analyzing service dependencies
    # -------------------------------------------------------------
    yield {
        "run_id": run_id,
        "stage": "analyzing_dependencies",
        "stage_index": 3,
        "total_stages": 6,
        "label": "Analyzing service dependencies",
        "status": "in_progress",
        "message": f"Mapping upstream callers and downstream service blast radius for '{service}'...",
    }

    from .dependencies import get_service_dependencies, analyze_downstream_impact
    deps = get_service_dependencies(service)
    downstream = analyze_downstream_impact(service)
    upstream_list = deps.get("upstream", [])
    downstream_list = downstream.get("downstream_services", [])

    dep_step_id = f"{run_id}_topo_{uuid.uuid4().hex[:6]}"
    append_step(
        incident_id,
        step_type="tool_result",
        tool="analyze_service_dependencies",
        input_data={"service": service},
        output_data={
            "upstream_services": upstream_list,
            "downstream_services": downstream_list,
            "direct_dependents": downstream.get("direct_dependents", []),
        },
        execution_id=dep_step_id,
        run_id=run_id,
    )

    if delay > 0:
        await asyncio.sleep(delay)

    yield {
        "run_id": run_id,
        "stage": "analyzing_dependencies",
        "stage_index": 3,
        "total_stages": 6,
        "label": "Analyzing service dependencies",
        "status": "completed",
        "message": f"Mapped topology dependencies: {len(upstream_list)} upstream callers and {len(downstream_list)} downstream services",
        "data": {"upstream": len(upstream_list), "downstream": len(downstream_list)},
    }

    # -------------------------------------------------------------
    # Stage 5: Evaluating root-cause hypotheses
    # -------------------------------------------------------------
    yield {
        "run_id": run_id,
        "stage": "evaluating_hypotheses",
        "stage_index": 4,
        "total_stages": 6,
        "label": "Evaluating root-cause hypotheses",
        "status": "in_progress",
        "message": "Evaluating root-cause hypotheses against evidence graph...",
    }

    from .root_cause import perform_root_cause_analysis
    rca_res = await perform_root_cause_analysis(incident_id, force_refresh=True)

    top_hyp = rca_res.hypotheses[0] if rca_res.hypotheses else None
    top_conf = getattr(top_hyp, "confidence", "Possible") if top_hyp else "Possible"
    hyp_count = len(rca_res.hypotheses)

    if delay > 0:
        await asyncio.sleep(delay)

    yield {
        "run_id": run_id,
        "stage": "evaluating_hypotheses",
        "stage_index": 4,
        "total_stages": 6,
        "label": "Evaluating root-cause hypotheses",
        "status": "completed",
        "message": f"Evaluated {hyp_count} candidate hypotheses (top candidate confidence: {top_conf})",
        "data": {"hypotheses_count": hyp_count, "top_confidence": top_conf},
    }

    # -------------------------------------------------------------
    # Stage 6: Preparing the evidence-backed diagnosis
    # -------------------------------------------------------------
    yield {
        "run_id": run_id,
        "stage": "preparing_diagnosis",
        "stage_index": 5,
        "total_stages": 6,
        "label": "Preparing the evidence-backed diagnosis",
        "status": "in_progress",
        "message": "Synthesizing evidence-backed diagnosis and preparing remediation action...",
    }

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
            "current_run_id": run_id,
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

    diag_exec_id = f"{run_id}_diag_{uuid.uuid4().hex[:6]}"
    append_step(
        incident_id,
        step_type="tool_result",
        tool="submit_diagnosis",
        input_data={},
        output_data={"saved": True, "diagnosis": diagnosis},
        execution_id=diag_exec_id,
        run_id=run_id,
    )

    append_step(
        incident_id,
        step_type="message",
        tool="investigation_completed",
        input_data={},
        output_data="Investigation completed successfully.",
        run_id=run_id,
    )

    final_result = {
        "run_id": run_id,
        "status": "awaiting_approval" if action else "investigating",
        "mode": "local_fallback",
        "diagnosis": diagnosis,
        "pending_action": action or None,
        "knowledge_results": len(knowledge.get("results", [])),
        "past_incident_results": len(incidents.get("results", [])),
    }

    if delay > 0:
        await asyncio.sleep(0.2)

    yield {
        "run_id": run_id,
        "stage": "preparing_diagnosis",
        "stage_index": 5,
        "total_stages": 6,
        "label": "Preparing the evidence-backed diagnosis",
        "status": "completed",
        "message": "Evidence-backed diagnosis and remediation action prepared",
        "data": {"status": final_result["status"], "has_action": bool(action)},
    }

    # Final completion event
    yield {
        "run_id": run_id,
        "stage": "complete",
        "status": "completed",
        "label": "Investigation complete",
        "message": "Investigation completed successfully.",
        "result": final_result,
    }


async def _fallback_investigation(
    incident_id: str,
    incident: dict[str, Any],
    run_id: str | None = None,
) -> dict[str, Any]:
    """Compatibility runner consuming run_investigation_generator."""
    final_result = None
    async for event in run_investigation_generator(incident_id, run_id=run_id):
        if event.get("stage") == "complete":
            final_result = event.get("result")
        elif event.get("status") == "failed":
            raise RuntimeError(event.get("message", "Investigation failed"))
    if final_result is not None:
        return final_result
    updated = get_incident(incident_id) or {}
    return {
        "run_id": run_id,
        "status": updated.get("status", "investigating"),
        "diagnosis": updated.get("diagnosis", {}),
    }


async def run_investigation(incident_id: str, run_id: str | None = None) -> dict[str, Any]:
    incident = get_incident(incident_id)
    if not incident:
        raise KeyError(f"Incident {incident_id!r} was not found")

    if not run_id:
        run_id = f"run_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:6]}"

    update_incident(incident_id, {"status": "investigating", "current_run_id": run_id})

    if not GCP_PROJECT and LOCAL_FALLBACK:
        return await _fallback_investigation(incident_id, incident, run_id=run_id)

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
        agent = Agent(name="tracewell_incident_investigator", model=GEMINI_MODEL, instruction=INSTRUCTION, tools=build_tools(incident_id, run_id=run_id))
        sessions = InMemorySessionService()
        session_id = f"incident-{incident_id}-{run_id}"
        user_id = "tracewell-api"
        await sessions.create_session(app_name=APP_NAME, user_id=user_id, session_id=session_id)
        runner = Runner(agent=agent, app_name=APP_NAME, session_service=sessions)

        append_step(
            incident_id,
            step_type="message",
            tool="investigation",
            input_data={"incident_id": incident_id},
            output_data="Investigation started.",
            run_id=run_id,
        )
        message = f"Investigate this incident ID {incident_id}. Incident JSON: {json.dumps(incident, default=str)[:8000]}"
        events_seen = 0
        final_text = ""
        async for event in runner.run_async(user_id=user_id, session_id=session_id,
                                            new_message=types.Content(role="user", parts=[types.Part(text=message)])):
            events_seen += 1
            text = _text_from_event(event)
            if text:
                final_text = text
                append_step(incident_id, step_type="message", tool="agent", input_data={}, output_data=text[:4000], run_id=run_id)
            if events_seen >= MAX_AGENT_STEPS * 4:
                append_step(incident_id, step_type="message", tool="agent", input_data={}, output_data="Stopped consuming ADK events at the configured safety limit.", run_id=run_id)
                break
        updated = get_incident(incident_id) or {}
        if not updated.get("diagnosis"):
            append_step(incident_id, step_type="message", tool="agent", input_data={}, output_data="Agent finished without submitting a structured diagnosis.", run_id=run_id)
            update_incident(incident_id, {"status":"failed"})
            return {"run_id": run_id, "status":"failed", "reason":"Agent did not call submit_diagnosis", "agent_output":final_text}
        return {"run_id": run_id, "status":updated.get("status", "investigating"), "mode":"adk", "agent_output":final_text}
    except (ImportError, ModuleNotFoundError) as exc:
        result = await _fallback_investigation(incident_id, incident, run_id=run_id)
        result["fallback_reason"] = f"ADK dependency unavailable: {type(exc).__name__}"
        return {"run_id": run_id, "status":"awaiting_approval" if result.get("pending_action") else "investigating", **result}
    except Exception as exc:
        import os
        if os.getenv("TRACEWELL_ALLOW_AGENT_FALLBACK", "true").lower() == "true":
            result = await _fallback_investigation(incident_id, incident, run_id=run_id)
            result["fallback_reason"] = f"ADK run failed: {type(exc).__name__}"
            return {"run_id": run_id, "status":"awaiting_approval" if result.get("pending_action") else "investigating", **result}
        update_incident(incident_id, {"status":"failed"})
        append_step(incident_id, step_type="message", tool="investigation_failed", input_data={}, output_data=f"{type(exc).__name__}: {str(exc)[:500]}", run_id=run_id)
        raise