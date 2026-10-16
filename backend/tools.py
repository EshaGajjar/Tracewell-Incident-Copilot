"""ADK tool factory. Remediation is intentionally NOT exposed as an agent tool."""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
import os
from typing import Any, Callable
import requests
from .config import GCP_PROJECT, GCP_REGION, LOG_LOOKBACK_MINUTES
from .retrieval import search_knowledge, search_past_incidents
from .store import append_step


def _safe_text(value: Any, limit: int = 7000) -> str:
    text = str(value)
    return text if len(text) <= limit else text[:limit] + "…[truncated]"


def build_tools(incident_id: str) -> list[Callable]:
    def recorded(name: str, fn: Callable, args: dict[str, Any]) -> dict[str, Any]:
        append_step(incident_id, step_type="tool_call", tool=name, input_data=args, output_data={"status": "started"})
        try:
            result = fn(**args)
            append_step(incident_id, step_type="tool_result", tool=name, input_data=args, output_data=result)
            return result
        except Exception as exc:
            result = {"error": f"{type(exc).__name__}: {str(exc)[:400]}"}
            append_step(incident_id, step_type="tool_result", tool=name, input_data=args, output_data=result)
            return result

    def knowledge_tool(query: str) -> dict:
        """Search the internal service runbooks, configuration guidance, and troubleshooting documents."""
        return recorded("search_knowledge", search_knowledge, {"query": query, "limit": 4})

    def incident_tool(query: str) -> dict:
        """Search historical incidents for similar symptoms, root causes, and fixes."""
        return recorded("search_past_incidents", search_past_incidents, {"query": query, "limit": 4})

    def deployment_tool(service: str) -> dict:
        """Inspect Cloud Run service URL and deployed revisions, traffic percentages, and timestamps."""
        def run(service: str) -> dict:
            if not GCP_PROJECT:
                return {"error": "GCP_PROJECT is not configured; deployment inspection requires Google Cloud credentials."}
            from google.cloud import run_v2
            client = run_v2.ServicesClient()
            name = f"projects/{GCP_PROJECT}/locations/{GCP_REGION}/services/{service}"
            svc = client.get_service(name=name)
            rev_client = run_v2.RevisionsClient()
            revisions = []
            for rev in rev_client.list_revisions(parent=name):
                revisions.append({"name": rev.name.split("/")[-1], "created_at": rev.create_time.isoformat() if rev.create_time else None,
                                  "ready": bool(rev.conditions and any(c.type_ == "Ready" and c.state.name == "CONDITION_SUCCEEDED" for c in rev.conditions)),
                                  "service": service})
            traffic = []
            for item in svc.traffic:
                traffic.append({"revision": item.revision, "latest_revision": item.latest_revision, "percent": item.percent, "tag": item.tag})
            return {"service": service, "uri": svc.uri, "traffic": traffic, "revisions": sorted(revisions, key=lambda x: x.get("created_at") or "", reverse=True)[:10]}
        return recorded("get_deployment_info", run, {"service": service})

    def logs_tool(service: str, minutes: int = LOG_LOOKBACK_MINUTES) -> dict:
        """Query recent Cloud Logging error entries for a Cloud Run service. Returns real log entries when configured."""
        def run(service: str, minutes: int) -> dict:
            if not GCP_PROJECT:
                return {"error": "GCP_PROJECT is not configured; log inspection requires Google Cloud credentials."}
            from google.cloud import logging as cloud_logging
            client = cloud_logging.Client(project=GCP_PROJECT)
            since = datetime.now(timezone.utc) - timedelta(minutes=max(1, min(minutes, 180)))
            filter_ = (f'resource.type="cloud_run_revision" AND resource.labels.service_name="{service}" '
                       f'AND timestamp>="{since.isoformat()}" AND (severity>=ERROR OR textPayload:"ERROR" OR jsonPayload.message:"ERROR")')
            entries = []
            for entry in client.list_entries(filter_=filter_, order_by="DESC", page_size=50):
                payload = entry.payload
                entries.append({"timestamp": entry.timestamp.isoformat() if entry.timestamp else None,
                                "severity": entry.severity, "payload": _safe_text(payload, 1200),
                                "revision": (entry.resource.labels or {}).get("revision_name") if entry.resource else None})
                if len(entries) >= 30:
                    break
            return {"service": service, "lookback_minutes": minutes, "count": len(entries), "entries": entries,
                    "query": filter_, "source": "Cloud Logging"}
        return recorded("get_logs", run, {"service": service, "minutes": minutes})

    def health_tool(service: str) -> dict:
        """Check the configured service health endpoint; this is a read-only probe."""
        def run(service: str) -> dict:
            url = os.getenv(f"SERVICE_URL_{service.upper().replace('-', '_')}") or os.getenv("PAYMENT_API_URL")
            if not url and GCP_PROJECT:
                try:
                    from google.cloud import run_v2
                    svc = run_v2.ServicesClient().get_service(name=f"projects/{GCP_PROJECT}/locations/{GCP_REGION}/services/{service}")
                    url = svc.uri
                except Exception as exc:
                    return {"service": service, "healthy": False, "error": f"Could not resolve service URL: {type(exc).__name__}"}
            if not url:
                return {"service": service, "healthy": False, "error": "Set PAYMENT_API_URL or SERVICE_URL_<SERVICE> to enable health checks."}
            try:
                response = requests.get(url.rstrip("/") + "/health", timeout=5)
                return {"service": service, "url": url, "healthy": response.ok, "status_code": response.status_code,
                        "body": _safe_text(response.text, 500)}
            except requests.RequestException as exc:
                return {"service": service, "url": url, "healthy": False, "error": f"{type(exc).__name__}: {str(exc)[:250]}"}
        return recorded("check_service_health", run, {"service": service})

    def propose_action(action: str, reason: str, target_revision: str = "") -> dict:
        """Record a proposed remediation for human review. This tool NEVER executes a rollback or changes traffic."""
        from .store import update_incident
        pending = {"action": action, "reason": reason, "target_revision": target_revision or None,
                   "status": "pending", "proposed_at": datetime.now(timezone.utc).isoformat()}
        update_incident(incident_id, {"pending_action": pending, "status": "awaiting_approval"})
        return recorded("propose_action", lambda **_: {"status": "pending_approval", "action": pending},
                        {"action": action, "reason": reason, "target_revision": target_revision})

    def submit_diagnosis(root_cause: str, category: str, severity: str, evidence: list[dict],
                         evidence_strength: str, recommended_action: str = "", target_revision: str = "",
                         unknowns: list[str] | None = None) -> dict:
        """Persist the structured diagnosis. Evidence strength must be Confirmed, Probable, or Possible."""
        from .store import save_diagnosis, update_incident
        allowed = {"Confirmed", "Probable", "Possible"}
        strength = evidence_strength if evidence_strength in allowed else "Possible"
        safe_evidence = [{"type": str(x.get("type", "unknown"))[:80], "detail": str(x.get("detail", ""))[:1000]} for x in (evidence or [])[:12]]
        diagnosis = {"root_cause": root_cause[:2000], "category": category[:80], "severity": severity[:20],
                     "evidence": safe_evidence, "evidence_strength": strength,
                     "unknowns": [str(x)[:500] for x in (unknowns or [])[:10]]}
        save_diagnosis(incident_id, diagnosis)
        update_incident(incident_id, {"category": category[:80], "severity": severity[:20], "root_cause": root_cause[:2000]})
        result = {"saved": True, "diagnosis": diagnosis}
        append_step(incident_id, step_type="tool_result", tool="submit_diagnosis", input_data={"category": category, "severity": severity}, output_data=result)
        # The agent should call propose_action separately when it wants approval.
        # Keeping diagnosis persistence side-effect-free avoids duplicate proposals.
        return result

    # Deliberately no rollback / set_traffic tool here. Remediation is a separate API flow.
    return [knowledge_tool, incident_tool, deployment_tool, logs_tool, health_tool, propose_action, submit_diagnosis]
