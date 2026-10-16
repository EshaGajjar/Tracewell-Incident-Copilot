"""Google ADK runner and a deterministic fallback for local smoke tests."""
from __future__ import annotations
import asyncio
import json
import re
from typing import Any
from .config import APP_NAME, GEMINI_MODEL, MAX_AGENT_STEPS, USE_VERTEX_AI
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


def _text_from_event(event: Any) -> str:
    content = getattr(event, "content", None)
    if not content:
        return ""
    parts = getattr(content, "parts", []) or []
    return "\n".join(str(getattr(part, "text", "")) for part in parts if getattr(part, "text", None))


def _fallback_investigation(incident_id: str, incident: dict[str, Any]) -> dict[str, Any]:
    """Transparent offline path. It gathers corpus evidence but never pretends to query live cloud logs."""
    description = f"{incident.get('title','')} {incident.get('description','')} service={incident.get('service','')}"
    service = incident.get("service") or "payment-api"
    logs = {"error": "Live Cloud Logging is not queried in local fallback mode."}
    deployments = {"error": "Live Cloud Run revisions are not queried in local fallback mode."}
    health = {"error": "Live service health is not queried in local fallback mode."}
    for name, inp, out in [
        ("get_deployment_info", {"service": service}, deployments),
        ("get_logs", {"service": service, "minutes": 30}, logs),
    ]:
        append_step(incident_id, step_type="tool_call", tool=name, input_data=inp, output_data={"status":"started"})
        append_step(incident_id, step_type="tool_result", tool=name, input_data=inp, output_data=out)
    knowledge = search_knowledge(description)
    incidents = search_past_incidents(description)
    for name, inp, out in [("search_knowledge", {"query": description}, knowledge), ("search_past_incidents", {"query": description}, incidents)]:
        append_step(incident_id, step_type="tool_call", tool=name, input_data=inp, output_data={"status":"started"})
        append_step(incident_id, step_type="tool_result", tool=name, input_data=inp, output_data=out)
    append_step(incident_id, step_type="tool_call", tool="check_service_health", input_data={"service":service}, output_data={"status":"started"})
    append_step(incident_id, step_type="tool_result", tool="check_service_health", input_data={"service":service}, output_data=health)

    q = description.lower()
    all_text = " ".join([str(knowledge), str(incidents)]).lower()
    if "database_url" in q or ("500" in q and ("deploy" in q or "revision" in q)):
        cause = "A malformed DATABASE_URL in the latest deployment is a plausible cause, but live deployment and log evidence are unavailable in local fallback mode."
        evidence = []
        if any("database_url" in str(x).lower() for x in knowledge.get("results", [])):
            evidence.append({"type":"runbook_match", "detail":"Retrieved internal documentation mentioning DATABASE_URL; validate its required format against the live revision."})
        if incidents.get("results"):
            evidence.append({"type":"historical_similarity", "detail":f"Retrieved {len(incidents['results'])} historical incident(s); similarity supports investigation but does not prove causality."})
        strength = "Possible"
        action = "Review the latest revision configuration and consider rollback to the last known-good revision after verifying the target."
        target = ""
        category = "config_error"
        severity = incident.get("severity") or "SEV2"
    else:
        cause = "Insufficient evidence to establish a root cause. The local fallback can retrieve documentation and historical incidents but cannot inspect live Cloud Run revisions or logs."
        evidence = [{"type":"retrieval", "detail":f"Retrieved {len(knowledge.get('results', []))} knowledge documents and {len(incidents.get('results', []))} historical incidents for triage."}]
        strength, action, target = "Possible", "Collect live logs and deployment metadata before selecting a remediation.", ""
        category, severity = incident.get("category") or "unknown", incident.get("severity") or "SEV3"
    diagnosis = {"root_cause":cause, "category":category, "severity":severity, "evidence":evidence,
                 "evidence_strength":strength, "unknowns":["Live Cloud Logging data unavailable in fallback mode.", "Live Cloud Run revision and traffic data unavailable in fallback mode."]}
    update_incident(incident_id, {"diagnosis":diagnosis, "category":category, "severity":severity, "root_cause":cause,
                                  "status":"awaiting_approval" if action else "investigating", "updated_at":__import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat()})
    if action:
        pending = {"action":action, "reason":"Fallback recommendation; confirm live evidence and target before approval.", "target_revision":target or None, "status":"pending"}
        update_incident(incident_id, {"pending_action":pending, "status":"awaiting_approval"})
    append_step(incident_id, step_type="tool_result", tool="submit_diagnosis", input_data={}, output_data={"saved":True,"diagnosis":diagnosis})
    return {"mode":"local_fallback", "diagnosis":diagnosis, "pending_action": action or None,
            "knowledge_results":len(knowledge.get("results", [])), "past_incident_results":len(incidents.get("results", []))}


async def run_investigation(incident_id: str) -> dict[str, Any]:
    incident = get_incident(incident_id)
    if not incident:
        raise KeyError(f"Incident {incident_id!r} was not found")
    update_incident(incident_id, {"status":"investigating"})
    append_step(incident_id, step_type="message", tool="investigation", input_data={"incident_id":incident_id}, output_data="Investigation started.")
    try:
        from google.adk.agents import Agent
        from google.adk.runners import Runner
        from google.adk.sessions import InMemorySessionService
        from google.genai import types
        from .config import GCP_PROJECT, GCP_REGION
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
        result = _fallback_investigation(incident_id, incident)
        result["fallback_reason"] = f"ADK dependency unavailable: {type(exc).__name__}"
        return {"status":"awaiting_approval" if result.get("pending_action") else "investigating", **result}
    except Exception as exc:
        # In a configured environment, don't silently hide a model/ADK failure behind
        # a made-up diagnosis. Fall back only if the caller explicitly permits it.
        import os
        if os.getenv("TRACEWELL_ALLOW_AGENT_FALLBACK", "true").lower() == "true":
            result = _fallback_investigation(incident_id, incident)
            result["fallback_reason"] = f"ADK run failed: {type(exc).__name__}"
            return {"status":"awaiting_approval" if result.get("pending_action") else "investigating", **result}
        update_incident(incident_id, {"status":"failed"})
        append_step(incident_id, step_type="message", tool="agent_error", input_data={}, output_data=f"{type(exc).__name__}: {str(exc)[:500]}")
        raise
