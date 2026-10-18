"""ADK tool factory. Remediation is intentionally NOT exposed as an agent tool."""
from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

import requests

from .config import GCP_PROJECT, GCP_REGION, LOG_LOOKBACK_MINUTES
from .retrieval import search_knowledge, search_past_incidents
from .store import append_step, append_operational_event, list_operational_events


def _safe_text(value: Any, limit: int = 7000) -> str:
    text = str(value)
    return text if len(text) <= limit else text[:limit] + "…[truncated]"


def _stable_key(*parts: Any) -> str:
    """Create a stable key from structured event identity."""
    raw = json.dumps(parts, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def build_tools(incident_id: str, run_id: str | None = None) -> list[Callable]:
    def recorded(name: str, fn: Callable, args: dict[str, Any]) -> dict[str, Any]:
        execution_id = f"{run_id + '_' if run_id else ''}exec_{uuid.uuid4().hex[:12]}"
        append_step(
            incident_id,
            step_type="tool_call",
            tool=name,
            input_data=args,
            output_data={"status": "started"},
            execution_id=execution_id,
            run_id=run_id,
        )
        try:
            result = fn(**args)
            append_step(
                incident_id,
                step_type="tool_result",
                tool=name,
                input_data=args,
                output_data=result,
                execution_id=execution_id,
                run_id=run_id,
            )
            return result
        except Exception as exc:
            result = {"error": f"{type(exc).__name__}: {str(exc)[:400]}"}
            append_step(
                incident_id,
                step_type="tool_result",
                tool=name,
                input_data=args,
                output_data=result,
                execution_id=execution_id,
                run_id=run_id,
            )
            return result

    def persist_event(
        event_type: str,
        service: str,
        description: str,
        event_timestamp: str | None = None,
        evidence: dict[str, Any] | None = None,
        dedupe_key: str | None = None,
    ) -> None:
        """Persist collected operational evidence for the incident timeline."""
        try:
            timestamp = None

            if event_timestamp:
                try:
                    timestamp = datetime.fromisoformat(
                        event_timestamp.replace("Z", "+00:00")
                    ).isoformat()
                except (TypeError, ValueError):
                    timestamp = None

            append_operational_event(
                incident_id=incident_id,
                event_type=event_type,
                service=service,
                description=description[:1500],
                event_timestamp=timestamp,
                evidence=evidence or {},
                dedupe_key=dedupe_key,
            )
        except Exception:
            # Timeline persistence must not break read-only evidence collection.
            pass

    def reporter_evidence_tool() -> dict:
        """Read the original manual report and text attachments already stored for this incident."""
        def run() -> dict:
            events = list_operational_events(incident_id)
            selected = [
                event for event in events
                if event.get("event_type") in {"user_report", "user_attachment"}
            ]
            return {
                "incident_id": incident_id,
                "count": len(selected),
                "source": "reporter-provided evidence",
                "events": [
                    {
                        "event_type": event.get("event_type"),
                        "timestamp": event.get("timestamp"),
                        "service": event.get("service"),
                        "description": _safe_text(event.get("description", ""), 5000),
                        "evidence": event.get("evidence", {}),
                    }
                    for event in selected[:8]
                ],
            }
        return recorded("get_reporter_evidence", run, {})

    def knowledge_tool(query: str) -> dict:
        """Search internal runbooks and troubleshooting documentation."""
        return recorded(
            "search_knowledge",
            search_knowledge,
            {"query": query, "limit": 4},
        )

    def incident_tool(query: str) -> dict:
        """Search historical incidents for similar symptoms and fixes."""
        return recorded(
            "search_past_incidents",
            search_past_incidents,
            {"query": query, "limit": 4},
        )

    def deployment_tool(service: str) -> dict:
        """Inspect Cloud Run service URL, revisions, traffic, and timestamps."""

        def run(service: str) -> dict:
            from .ingestion import (
                fetch_cloud_run_deployments,
                associate_and_persist_operational_events,
            )

            res = fetch_cloud_run_deployments(service)
            if res.get("status") == "error":
                return {"error": res.get("error")}

            associate_and_persist_operational_events(
                incident_id,
                deployments_data=res,
            )

            return {
                "service": service,
                "uri": res.get("uri"),
                "traffic": res.get("traffic", []),
                "revisions": res.get("revisions", []),
            }

        return recorded(
            "get_deployment_info",
            run,
            {"service": service},
        )

    def logs_tool(
        service: str,
        minutes: int = LOG_LOOKBACK_MINUTES,
    ) -> dict:
        """Query recent Cloud Logging error entries for a Cloud Run service."""

        def run(service: str, minutes: int) -> dict:
            from .ingestion import (
                fetch_cloud_logging_errors,
                associate_and_persist_operational_events,
            )

            res = fetch_cloud_logging_errors(
                service,
                minutes=minutes,
                incident_id=incident_id,
            )
            if res.get("status") == "error":
                return {"error": res.get("error")}

            associate_and_persist_operational_events(
                incident_id,
                errors_data=res,
            )

            return {
                "service": service,
                "lookback_minutes": minutes,
                "count": res.get("count", 0),
                "entries": res.get("entries", []),
                "query": res.get("query"),
                "source": "Cloud Logging",
            }

        return recorded(
            "get_logs",
            run,
            {"service": service, "minutes": minutes},
        )

    def health_tool(service: str) -> dict:
        """Check the configured service health endpoint; this is a read-only probe."""

        def run(service: str) -> dict:
            url = (
                os.getenv(f"SERVICE_URL_{service.upper().replace('-', '_')}")
                or os.getenv("PAYMENT_API_URL")
            )

            if not url and GCP_PROJECT:
                try:
                    from google.cloud import run_v2

                    svc = run_v2.ServicesClient().get_service(
                        name=(
                            f"projects/{GCP_PROJECT}/locations/{GCP_REGION}"
                            f"/services/{service}"
                        )
                    )
                    url = svc.uri
                except Exception as exc:
                    return {
                        "service": service,
                        "healthy": False,
                        "error": (
                            f"Could not resolve service URL: "
                            f"{type(exc).__name__}"
                        ),
                    }

            if not url:
                return {
                    "service": service,
                    "healthy": False,
                    "error": (
                        "Set PAYMENT_API_URL or SERVICE_URL_<SERVICE> "
                        "to enable health checks."
                    ),
                }

            try:
                response = requests.get(
                    url.rstrip("/") + "/health",
                    timeout=5,
                )
                return {
                    "service": service,
                    "url": url,
                    "healthy": response.ok,
                    "status_code": response.status_code,
                    "body": _safe_text(response.text, 500),
                }
            except requests.RequestException as exc:
                return {
                    "service": service,
                    "healthy": False,
                    "error": f"{type(exc).__name__}: {str(exc)[:250]}",
                }

        return recorded(
            "check_service_health",
            run,
            {"service": service},
        )

    def propose_action(
        action: str,
        reason: str,
        target_revision: str = "",
    ) -> dict:
        """Record a proposed remediation for human review; never execute it."""
        from .store import update_incident

        now = datetime.now(timezone.utc).isoformat()
        pending = {
            "action": action,
            "reason": reason,
            "target_revision": target_revision or None,
            "status": "pending",
            "lifecycle_state": "pending_approval",
            "proposed_at": now,
        }

        history = [
            {
                "event": "proposed",
                "timestamp": now,
                "action": action,
                "reason": reason,
                "target_revision": target_revision or None,
            }
        ]

        update_incident(
            incident_id,
            {
                "pending_action": pending,
                "action_history": history,
                "status": "awaiting_approval",
            },
        )

        return recorded(
            "propose_action",
            lambda **_: {
                "status": "pending_approval",
                "action": pending,
            },
            {
                "action": action,
                "reason": reason,
                "target_revision": target_revision,
            },
        )

    def submit_diagnosis(
        root_cause: str,
        category: str,
        severity: str,
        evidence: list[dict],
        evidence_strength: str,
        recommended_action: str = "",
        target_revision: str = "",
        unknowns: Optional[list[str]] = None,
    ) -> dict:
        """Persist the structured diagnosis."""
        from .store import save_diagnosis, update_incident

        allowed = {"Confirmed", "Probable", "Possible"}
        strength = (
            evidence_strength
            if evidence_strength in allowed
            else "Possible"
        )

        safe_evidence = [
            {
                "type": str(item.get("type", "unknown"))[:80],
                "detail": str(item.get("detail", ""))[:1000],
            }
            for item in (evidence or [])[:12]
        ]

        diagnosis = {
            "root_cause": root_cause[:2000],
            "category": category[:80],
            "severity": severity[:20],
            "evidence": safe_evidence,
            "evidence_strength": strength,
            "unknowns": [
                str(item)[:500]
                for item in (unknowns or [])[:10]
            ],
        }

        save_diagnosis(incident_id, diagnosis)

        update_incident(
            incident_id,
            {
                "category": category[:80],
                "severity": severity[:20],
                "root_cause": root_cause[:2000],
            },
        )

        result = {
            "saved": True,
            "diagnosis": diagnosis,
        }

        append_step(
            incident_id,
            step_type="tool_result",
            tool="submit_diagnosis",
            input_data={
                "category": category,
                "severity": severity,
            },
            output_data=result,
        )

        # The agent should call propose_action separately when needed.
        return result

    # Deliberately no rollback / set_traffic tool here.
    return [
        reporter_evidence_tool,
        knowledge_tool,
        incident_tool,
        deployment_tool,
        logs_tool,
        health_tool,
        propose_action,
        submit_diagnosis,
    ]