"""Timeline presentation and deduplication engine for Tracewell Incident Copilot.

Filters redundant tool-call start events while preserving:
1. Completed tool results with full evidence and summaries.
2. Meaningful lifecycle events (investigation started, diagnosis submitted, completed, failed).
3. Genuinely separate executions and retries with distinct results.
4. Historical steps in backend storage (presentation/API layer filtering only).
"""
from __future__ import annotations

import json
from typing import Any

LIFECYCLE_TOOLS = {
    "investigation",
    "submit_diagnosis",
    "investigation_completed",
    "investigation_failed",
    "agent_error",
}

LIFECYCLE_TYPES = {
    "message",
    "lifecycle",
    "diagnosis",
}


def is_lifecycle_step(step: dict[str, Any]) -> bool:
    tool = str(step.get("tool") or "").strip().lower()
    step_type = str(step.get("type") or "").strip().lower()

    if tool in LIFECYCLE_TOOLS:
        return True
    if step_type in LIFECYCLE_TYPES:
        return True
    return False


def generate_step_summary(step: dict[str, Any]) -> str:
    """Generate a clean, readable result summary for a completed step."""
    output = step.get("output")
    tool = str(step.get("tool") or "")
    step_type = str(step.get("type") or "")

    if output is None:
        return "No result recorded."

    if isinstance(output, str):
        return output.strip()[:200]

    if isinstance(output, dict):
        if "error" in output:
            return f"Error: {output['error']}"
        if output.get("status") == "started":
            return "Tool execution started."
        if tool == "get_deployment_info":
            revs = output.get("revisions") or output.get("results")
            if isinstance(revs, list):
                return f"Retrieved {len(revs)} deployment revision(s) for service."
            return "Deployment metadata retrieved."
        if tool == "get_logs":
            logs = output.get("logs") or output.get("results")
            if isinstance(logs, list):
                return f"Analyzed {len(logs)} log entries in investigation window."
            return "Log entries retrieved."
        if tool == "search_knowledge":
            docs = output.get("results")
            if isinstance(docs, list):
                return f"Retrieved {len(docs)} matching runbook/knowledge document(s)."
            return "Knowledge base search completed."
        if tool == "search_past_incidents":
            incidents = output.get("results")
            if isinstance(incidents, list):
                return f"Found {len(incidents)} similar historical incident(s)."
            return "Historical incident search completed."
        if tool == "check_service_health":
            status = output.get("status") or output.get("health_status") or "normal"
            return f"Service health check result: {status}."
        if tool == "submit_diagnosis":
            diag = output.get("diagnosis", {})
            cause = diag.get("root_cause") or "Diagnosis submitted."
            return str(cause)[:200]
        if "summary" in output:
            return str(output["summary"])[:200]
        if "message" in output:
            return str(output["message"])[:200]

        # Generic summary from dict keys
        keys = [k for k in output.keys() if k != "status"]
        if keys:
            return f"Completed ({', '.join(keys[:3])})."
        return "Completed with result."

    return str(output)[:200]


def is_started_event(output: Any) -> bool:
    """Check if output represents only an uncompleted start marker."""
    if output == {"status": "started"}:
        return True
    if isinstance(output, dict) and output.get("status") == "started" and len(output) == 1:
        return True
    return False


def filter_timeline_steps(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Filter raw steps for timeline display.
    
    Associates tool_call and tool_result by:
    1. execution_id when present.
    2. Sequentially matching (tool, input) pairing for legacy data.
    
    Hides redundant start events when a completed result exists.
    Preserves all lifecycle events, in-flight/uncompleted calls, and retries.
    """
    if not steps:
        return []

    # Identify execution_ids that have completed results
    completed_execution_ids: set[str] = set()
    for step in steps:
        step_type = str(step.get("type") or "")
        exec_id = step.get("execution_id")
        if step_type == "tool_result" and exec_id:
            completed_execution_ids.add(str(exec_id))

    # Track legacy matching: for steps without execution_id
    # We find if there is a later tool_result for this tool with meaningful output
    # Pre-index tool_results without execution_id
    legacy_results: list[dict[str, Any]] = [
        s for s in steps
        if str(s.get("type")) == "tool_result" and not s.get("execution_id")
    ]
    used_legacy_result_indices: set[int] = set()

    visible_steps: list[dict[str, Any]] = []

    for idx, step in enumerate(steps):
        step_type = str(step.get("type") or "")
        tool = str(step.get("tool") or "")
        exec_id = step.get("execution_id")
        output = step.get("output")

        # Lifecycle events are always preserved
        if is_lifecycle_step(step):
            step_copy = dict(step)
            step_copy["summary"] = generate_step_summary(step_copy)
            visible_steps.append(step_copy)
            continue

        # If it's a tool_call with started status:
        if step_type == "tool_call" and is_started_event(output):
            # Check if associated by execution_id
            if exec_id and str(exec_id) in completed_execution_ids:
                # Associated result exists; suppress redundant start event
                continue

            # Check legacy pairing
            if not exec_id:
                # Look for an available tool_result with the same tool
                paired = False
                for r_idx, r_step in enumerate(legacy_results):
                    if r_idx in used_legacy_result_indices:
                        continue
                    if r_step.get("tool") == tool:
                        # Match input if available
                        call_inp = json.dumps(step.get("input"), sort_keys=True, default=str)
                        res_inp = json.dumps(r_step.get("input"), sort_keys=True, default=str)
                        if call_inp == res_inp or not step.get("input"):
                            used_legacy_result_indices.add(r_idx)
                            paired = True
                            break
                if paired:
                    # Suppress redundant start event
                    continue

        # For completed tool_result or other meaningful steps: preserve and enrich with summary
        step_copy = dict(step)
        step_copy["summary"] = generate_step_summary(step_copy)
        visible_steps.append(step_copy)

    return visible_steps
