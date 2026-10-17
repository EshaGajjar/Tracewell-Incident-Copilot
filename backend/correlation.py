"""Evidence-based event correlation engine for Tracewell Incident Copilot.

Connects operational events, service dependencies, and incident context to classify
and explain events related to an incident.

STRICT PRINCIPLES:
1. Timestamp proximity alone must NEVER establish causation.
2. Causation requires direct service/revision matches or verified dependency relationships.
3. Classifications clearly distinguish 'Direct match', 'Dependency-related',
   'Temporally associated', and 'Unrelated'.
4. Preserves complete evidence without inventing missing information.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from .dependencies import (
    ensure_initialized,
    get_service_dependencies,
    list_dependency_relationships,
)
from .store import (
    get_incident,
    list_all_operational_events,
    list_correlated_events,
    list_operational_events,
    save_correlated_events_batch,
)

logger = logging.getLogger("tracewell.correlation")


# -------------------------------------------------------------------
# Data Models
# -------------------------------------------------------------------


class CorrelationClassification(str, Enum):
    DIRECT_MATCH = "direct_match"
    DEPENDENCY_RELATED = "dependency_related"
    TEMPORALLY_ASSOCIATED = "temporally_associated"
    UNRELATED = "unrelated"


class CorrelatedEvent(BaseModel):
    event_id: str = Field(description="Original operational event ID")
    incident_id: str = Field(description="Associated incident ID")
    timestamp: str = Field(description="Event timestamp in ISO format")
    service: str = Field(description="Service that emitted the event")
    revision: str | None = Field(default=None, description="Revision identifier if applicable")
    event_type: str = Field(description="Type of event: deployment, error_log, traffic_allocation, health_check")
    severity: str | None = Field(default="INFO", description="Severity level: ERROR, WARNING, INFO")
    description: str = Field(default="", description="Summary of the operational event")
    classification: CorrelationClassification = Field(description="Correlation classification")
    classification_label: str = Field(description="Human-readable classification label")
    correlation_reason: str = Field(description="Detailed explanation of why this event was correlated")
    evidence_source: str = Field(default="Cloud Logging", description="Source of the evidence")
    relationship_direction: str | None = Field(default=None, description="self | upstream_dependency | downstream_dependent | none")
    relationship_type: str | None = Field(default=None, description="Relationship type if dependency-related")
    relationship_verification_status: str | None = Field(default=None, description="verified | configured | none")
    confidence: str = Field(default="high", description="Confidence in correlation: high | medium | low")
    is_causal_candidate: bool = Field(default=False, description="Whether this event is a candidate cause of the incident")
    evidence: dict[str, Any] = Field(default_factory=dict, description="Original evidence payload")


class IncidentCorrelatedEventsResponse(BaseModel):
    incident_id: str
    incident_service: str
    incident_title: str
    total_events: int
    direct_matches_count: int
    dependency_related_count: int
    temporally_associated_count: int
    unrelated_count: int
    events: list[CorrelatedEvent]


# -------------------------------------------------------------------
# Helper: Timestamp normalization and window parsing
# -------------------------------------------------------------------


def _parse_iso_timestamp(ts: Any) -> datetime | None:
    if not ts:
        return None
    if isinstance(ts, datetime):
        return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    if isinstance(ts, str):
        try:
            cleaned = ts.replace("Z", "+00:00")
            dt = datetime.fromisoformat(cleaned)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            pass
    return None


def _extract_revision_from_event(event: dict[str, Any]) -> str | None:
    evidence = event.get("evidence") or {}
    if evidence.get("revision"):
        rev = evidence["revision"]
        if isinstance(rev, dict):
            return rev.get("name")
        return str(rev)
    if evidence.get("log", {}).get("revision"):
        return str(evidence["log"]["revision"])
    if "revision" in event:
        return str(event["revision"])
    return None


def _extract_severity_from_event(event: dict[str, Any]) -> str:
    evidence = event.get("evidence") or {}
    sev = (
        evidence.get("severity")
        or evidence.get("log", {}).get("severity")
        or event.get("severity")
    )
    if sev:
        return str(sev).upper()
    event_type = event.get("event_type", "")
    if event_type == "error_log":
        return "ERROR"
    if event_type in ("deployment", "traffic_allocation"):
        return "INFO"
    return "INFO"


# -------------------------------------------------------------------
# Core Correlation Classifier
# -------------------------------------------------------------------


def classify_event_for_incident(
    event: dict[str, Any],
    incident: dict[str, Any],
    upstream_deps: list[dict[str, Any]],
    downstream_deps: list[dict[str, Any]],
    target_revisions: set[str],
    window_start: datetime | None,
    window_end: datetime | None,
) -> CorrelatedEvent:
    """Classify a single operational event against incident context.

    ENFORCES STRICT RULES:
    1. Direct match: Exact service match or revision match.
    2. Dependency-related: Event belongs to an upstream provider or downstream caller.
    3. Temporally associated: Event occurred within incident window but has no dependency linkage.
       Explicitly states that temporal proximity does not prove causation.
    4. Unrelated: Different service outside relevant window.
    """
    event_id = str(event.get("id") or event.get("event_id") or "unknown_event")
    incident_id = str(incident.get("id", ""))
    event_service = str(event.get("service") or "").strip()
    incident_service = str(incident.get("service") or "").strip()
    event_type = str(event.get("event_type") or "operational_event")
    description = str(event.get("description") or "")
    evidence = event.get("evidence") or {}
    raw_timestamp = str(event.get("timestamp") or event.get("event_timestamp") or datetime.now(timezone.utc).isoformat())
    event_dt = _parse_iso_timestamp(raw_timestamp)
    revision = _extract_revision_from_event(event)
    severity = _extract_severity_from_event(event)

    # Determine if event falls in the temporal window
    in_time_window = False
    if event_dt and window_start and window_end:
        in_time_window = window_start <= event_dt <= window_end
    elif event_dt and window_start:
        in_time_window = event_dt >= window_start

    # Determine source of evidence
    evidence_source = str(
        evidence.get("source")
        or ("Cloud Logging" if event_type == "error_log" else "Cloud Run API" if event_type in ("deployment", "traffic_allocation") else "Operational Event Store")
    )

    # 1. Exact Service Match
    if event_service and incident_service and event_service.lower() == incident_service.lower():
        # Check for specific revision match
        if revision and revision in target_revisions:
            reason = (
                f"Exact revision match: Event occurred on incident service '{incident_service}' "
                f"specifically for target revision '{revision}' identified in incident context."
            )
            return CorrelatedEvent(
                event_id=event_id,
                incident_id=incident_id,
                timestamp=raw_timestamp,
                service=event_service,
                revision=revision,
                event_type=event_type,
                severity=severity,
                description=description,
                classification=CorrelationClassification.DIRECT_MATCH,
                classification_label="Direct match",
                correlation_reason=reason,
                evidence_source=evidence_source,
                relationship_direction="self",
                relationship_type="primary_service",
                relationship_verification_status="verified",
                confidence="high",
                is_causal_candidate=True,
                evidence=evidence,
            )

        reason = (
            f"Exact service match: {event_type.replace('_', ' ').capitalize()} event on the affected "
            f"incident service '{incident_service}' during triage."
        )
        is_causal = event_type in ("error_log", "deployment") and severity in ("ERROR", "CRITICAL")
        return CorrelatedEvent(
            event_id=event_id,
            incident_id=incident_id,
            timestamp=raw_timestamp,
            service=event_service,
            revision=revision,
            event_type=event_type,
            severity=severity,
            description=description,
            classification=CorrelationClassification.DIRECT_MATCH,
            classification_label="Direct match",
            correlation_reason=reason,
            evidence_source=evidence_source,
            relationship_direction="self",
            relationship_type="primary_service",
            relationship_verification_status="verified",
            confidence="high",
            is_causal_candidate=is_causal,
            evidence=evidence,
        )

    # 2. Dependency-Related (Upstream Dependencies)
    # Incident service depends on upstream service (e.g. payment-api depends on payment-db)
    upstream_match = next(
        (
            dep for dep in upstream_deps
            if (dep.get("target") or dep.get("target_service") or "").lower() == event_service.lower()
        ),
        None,
    )
    if upstream_match:
        rel_type = upstream_match.get("relationship_type", "dependency")
        ver_status = upstream_match.get("verification_status", "configured")
        reason = (
            f"Upstream dependency: Incident service '{incident_service}' depends on '{event_service}' "
            f"via {rel_type.replace('_', ' ')} ({ver_status}). Failure or latency in this provider "
            f"can cascade into '{incident_service}' as a potential root cause."
        )
        is_causal = event_type == "error_log" or severity in ("ERROR", "CRITICAL")
        return CorrelatedEvent(
            event_id=event_id,
            incident_id=incident_id,
            timestamp=raw_timestamp,
            service=event_service,
            revision=revision,
            event_type=event_type,
            severity=severity,
            description=description,
            classification=CorrelationClassification.DEPENDENCY_RELATED,
            classification_label="Dependency-related",
            correlation_reason=reason,
            evidence_source=evidence_source,
            relationship_direction="upstream_dependency",
            relationship_type=rel_type,
            relationship_verification_status=ver_status,
            confidence="high" if ver_status == "verified" else "medium",
            is_causal_candidate=is_causal,
            evidence=evidence,
        )

    # 3. Dependency-Related (Downstream Dependents)
    # Downstream service depends on incident service (e.g. order-service depends on payment-api)
    downstream_match = next(
        (
            dep for dep in downstream_deps
            if (dep.get("source") or dep.get("source_service") or "").lower() == event_service.lower()
        ),
        None,
    )
    if downstream_match:
        rel_type = downstream_match.get("relationship_type", "dependency")
        ver_status = downstream_match.get("verification_status", "configured")
        reason = (
            f"Downstream dependent: Consumer '{event_service}' depends on incident service '{incident_service}' "
            f"via {rel_type.replace('_', ' ')} ({ver_status}). Events on this consumer reflect cascading downstream "
            f"symptomatic impact rather than root cause."
        )
        return CorrelatedEvent(
            event_id=event_id,
            incident_id=incident_id,
            timestamp=raw_timestamp,
            service=event_service,
            revision=revision,
            event_type=event_type,
            severity=severity,
            description=description,
            classification=CorrelationClassification.DEPENDENCY_RELATED,
            classification_label="Dependency-related",
            correlation_reason=reason,
            evidence_source=evidence_source,
            relationship_direction="downstream_dependent",
            relationship_type=rel_type,
            relationship_verification_status=ver_status,
            confidence="high" if ver_status == "verified" else "medium",
            is_causal_candidate=False,
            evidence=evidence,
        )

    # 4. Temporally Associated (In time window, but no dependency relationship)
    if in_time_window:
        time_str = event_dt.strftime("%Y-%m-%d %H:%M:%S UTC") if event_dt else raw_timestamp
        reason = (
            f"Temporally associated only: Occurred at {time_str} within the incident observation window, "
            f"but service '{event_service}' has no known dependency relationship with '{incident_service}'. "
            f"Timestamp proximity alone does not establish causation."
        )
        return CorrelatedEvent(
            event_id=event_id,
            incident_id=incident_id,
            timestamp=raw_timestamp,
            service=event_service,
            revision=revision,
            event_type=event_type,
            severity=severity,
            description=description,
            classification=CorrelationClassification.TEMPORALLY_ASSOCIATED,
            classification_label="Temporally associated",
            correlation_reason=reason,
            evidence_source=evidence_source,
            relationship_direction="none",
            relationship_type=None,
            relationship_verification_status=None,
            confidence="low",
            is_causal_candidate=False,
            evidence=evidence,
        )

    # 5. Unrelated
    reason = (
        f"Unrelated: Service '{event_service}' has no dependency relationship with incident service "
        f"'{incident_service}' and the event occurred outside the active incident window."
    )
    return CorrelatedEvent(
        event_id=event_id,
        incident_id=incident_id,
        timestamp=raw_timestamp,
        service=event_service,
        revision=revision,
        event_type=event_type,
        severity=severity,
        description=description,
        classification=CorrelationClassification.UNRELATED,
        classification_label="Unrelated",
        correlation_reason=reason,
        evidence_source=evidence_source,
        relationship_direction="none",
        relationship_type=None,
        relationship_verification_status=None,
        confidence="low",
        is_causal_candidate=False,
        evidence=evidence,
    )


# -------------------------------------------------------------------
# Orchestrator: Correlate Events for Incident
# -------------------------------------------------------------------


def correlate_events_for_incident(
    incident_id: str,
    candidate_events: list[dict[str, Any]] | None = None,
    lookback_minutes: int = 60,
    persist: bool = True,
) -> IncidentCorrelatedEventsResponse:
    """Analyze and correlate operational events for an incident.

    Collects candidate events from:
    1. Explicit candidate_events passed by caller.
    2. Operational events recorded under this incident.
    3. Operational events across the store for upstream/downstream services.

    Deduplicates events, runs dependency-aware correlation, and optionally persists
    correlation results to the store without duplicating records.
    """
    ensure_initialized()

    incident = get_incident(incident_id)
    if not incident:
        raise KeyError(f"Incident '{incident_id}' was not found")

    incident_service = incident.get("service") or "unknown"
    incident_title = incident.get("title") or "Untitled Incident"

    # Gather target revisions from incident context
    target_revisions: set[str] = set()
    pending = incident.get("pending_action") or {}
    if pending.get("target_revision"):
        target_revisions.add(str(pending["target_revision"]))
    diagnosis = incident.get("diagnosis") or {}
    if diagnosis.get("target_revision"):
        target_revisions.add(str(diagnosis["target_revision"]))

    # Parse incident temporal window
    created_dt = _parse_iso_timestamp(incident.get("created_at"))
    if not created_dt:
        created_dt = datetime.now(timezone.utc)

    window_start = created_dt - timedelta(minutes=lookback_minutes)
    resolved_dt = _parse_iso_timestamp(incident.get("resolved_at"))
    if resolved_dt:
        window_end = resolved_dt + timedelta(minutes=15)
    else:
        window_end = created_dt + timedelta(minutes=45)

    # Retrieve dependency topology for the incident service
    deps_info = get_service_dependencies(incident_service, direction="both")
    upstream_deps = deps_info.get("upstream") or []
    downstream_deps = deps_info.get("downstream") or []

    # Gather candidate events
    raw_candidates: list[dict[str, Any]] = []

    # 1. Caller-supplied candidate events
    if candidate_events:
        raw_candidates.extend(candidate_events)

    # 2. Events explicitly recorded under this incident
    try:
        inc_events = list_operational_events(incident_id)
        raw_candidates.extend(inc_events)
    except Exception as exc:
        logger.warning("Could not load incident events: %s", exc)

    # 3. Events from related dependency services across the store within the temporal window
    try:
        all_store_events = list_all_operational_events(limit=300)
        # Filter to include events matching direct dependencies (upstream or downstream)
        # Primary service events belong to their specific incidents and must not be cross-pollinated
        dependency_services = set()
        for d in upstream_deps:
            target_svc = d.get("target") or d.get("target_service")
            if target_svc and target_svc.lower() != incident_service.lower():
                dependency_services.add(target_svc.lower())
        for d in downstream_deps:
            source_svc = d.get("source") or d.get("source_service")
            if source_svc and source_svc.lower() != incident_service.lower():
                dependency_services.add(source_svc.lower())

        for ev in all_store_events:
            ev_inc = ev.get("incident_id")
            if ev_inc and ev_inc == incident_id:
                continue
            ev_svc = (ev.get("service") or "").lower()
            if ev_svc in dependency_services:
                ev_dt = _parse_iso_timestamp(ev.get("timestamp") or ev.get("event_timestamp"))
                if ev_dt and window_start and window_end and (window_start <= ev_dt <= window_end):
                    raw_candidates.append(ev)
    except Exception as exc:
        logger.warning("Could not query store events for related services: %s", exc)

    # Deduplicate candidate events by unique ID
    unique_candidates: dict[str, dict[str, Any]] = {}
    for ev in raw_candidates:
        eid = str(ev.get("id") or ev.get("event_id") or "")
        if not eid:
            eid = f"synth_{hash(str(ev))}"
            ev["id"] = eid
        if eid not in unique_candidates:
            unique_candidates[eid] = ev

    # Classify each unique candidate event
    correlated: list[CorrelatedEvent] = []
    for ev in unique_candidates.values():
        item = classify_event_for_incident(
            event=ev,
            incident=incident,
            upstream_deps=upstream_deps,
            downstream_deps=downstream_deps,
            target_revisions=target_revisions,
            window_start=window_start,
            window_end=window_end,
        )
        correlated.append(item)

    # Sort correlated events:
    # 1. Direct matches first
    # 2. Dependency-related next
    # 3. Temporally associated
    # 4. Unrelated
    # Within each group, sort chronologically
    order_map = {
        CorrelationClassification.DIRECT_MATCH: 1,
        CorrelationClassification.DEPENDENCY_RELATED: 2,
        CorrelationClassification.TEMPORALLY_ASSOCIATED: 3,
        CorrelationClassification.UNRELATED: 4,
    }
    correlated.sort(
        key=lambda x: (
            order_map.get(x.classification, 5),
            x.timestamp,
        )
    )

    # Persist batch if requested
    if persist and correlated:
        try:
            save_correlated_events_batch(
                incident_id,
                [c.model_dump() for c in correlated],
            )
        except Exception as exc:
            logger.warning("Could not persist correlated events batch: %s", exc)

    direct_count = sum(1 for c in correlated if c.classification == CorrelationClassification.DIRECT_MATCH)
    dep_count = sum(1 for c in correlated if c.classification == CorrelationClassification.DEPENDENCY_RELATED)
    temp_count = sum(1 for c in correlated if c.classification == CorrelationClassification.TEMPORALLY_ASSOCIATED)
    unrelated_count = sum(1 for c in correlated if c.classification == CorrelationClassification.UNRELATED)

    return IncidentCorrelatedEventsResponse(
        incident_id=incident_id,
        incident_service=incident_service,
        incident_title=incident_title,
        total_events=len(correlated),
        direct_matches_count=direct_count,
        dependency_related_count=dep_count,
        temporally_associated_count=temp_count,
        unrelated_count=unrelated_count,
        events=correlated,
    )
