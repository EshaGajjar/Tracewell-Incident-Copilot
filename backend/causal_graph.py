"""Incident-specific causal graph construction and persistence.

Builds a causal graph connecting services, operational events (deployments,
errors, health checks), evidence references, and root-cause hypotheses.

STRICT PRINCIPLES:
1. Stable node and edge identifiers for deterministic reconstruction.
2. Differentiates configured vs verified vs inferred causal relationships.
3. Timestamp proximity alone must NEVER establish causation.
4. Never invent services, events, evidence IDs, or relationships.
5. If evidence is insufficient, graph displays topological services and events
   without fabricating causal edges.
6. Repeated retrievals do not create duplicate persistence records.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from .correlation import (
    CorrelatedEvent,
    CorrelationClassification,
    correlate_events_for_incident,
)
from .dependencies import (
    ensure_initialized,
    get_service_dependencies,
)
from .root_cause import perform_root_cause_analysis
from .store import (
    get_incident,
    get_investigation_graph,
    list_operational_events,
    save_investigation_graph,
)

logger = logging.getLogger("tracewell.causal_graph")


# -------------------------------------------------------------------
# Pydantic Graph Data Models
# -------------------------------------------------------------------


class CausalGraphNode(BaseModel):
    """Node representing an entity in the investigation causal graph."""
    id: str = Field(description="Stable node identifier (e.g. node_svc_payment-api, node_err_abc123)")
    entity_id: str = Field(description="Original entity or evidence ID")
    label: str = Field(description="Display label")
    node_type: str = Field(
        description="service | deployment | error_event | health_event | root_cause_hypothesis"
    )
    service: str = Field(description="Associated service name")
    timestamp: str | None = Field(default=None, description="ISO timestamp where applicable")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Entity attributes and context")
    evidence_ids: list[str] = Field(default_factory=list, description="Associated evidence IDs")
    is_leading_hypothesis: bool = Field(default=False, description="Whether this node is the leading root cause hypothesis")


class CausalGraphEdge(BaseModel):
    """Directional edge representing a relationship in the causal graph."""
    id: str = Field(description="Stable edge identifier")
    source: str = Field(description="Source node ID")
    target: str = Field(description="Target node ID")
    relationship_type: str = Field(
        description=(
            "causes | triggers_error | propagates_to | impacts | "
            "calls_service | queries_db | depends_on | deployed_to | "
            "occurred_on | checked_service | hypothesized_cause | evidence_for"
        )
    )
    explanation: str = Field(description="Human-readable explanation of why this relationship exists")
    supporting_evidence_ids: list[str] = Field(
        default_factory=list, description="IDs of real operational evidence supporting this edge"
    )
    verification_status: str = Field(
        description="verified | configured | inferred"
    )
    confidence: str | None = Field(
        default=None, description="Confidence level: Confirmed | Probable | Possible | High | Medium | Low"
    )


class CausalGraphResponse(BaseModel):
    """Structured response containing the complete incident investigation graph."""
    graph_id: str = Field(description="Stable identifier of the investigation graph")
    incident_id: str = Field(description="Incident ID")
    incident_service: str = Field(description="Primary service affected by the incident")
    incident_title: str = Field(description="Title of the incident")
    schema_version: str = Field(default="1.0", description="Schema version of the graph")
    status: str = Field(description="sufficient_evidence | insufficient_evidence")
    insufficient_evidence: bool = Field(description="True if evidence is insufficient to establish causal links")
    summary: str = Field(description="Summary of the graph and root-cause status")
    analysis_mode: str = Field(default="deterministic_local_fallback", description="Engine used for analysis")
    leading_hypothesis: dict[str, Any] | None = Field(
        default=None, description="Summary of rank #1 root-cause hypothesis if identified"
    )
    leading_hypothesis_id: str | None = Field(
        default=None, description="Node ID of rank #1 root-cause hypothesis if identified"
    )
    nodes: list[CausalGraphNode] = Field(default_factory=list, description="All nodes in the graph")
    edges: list[CausalGraphEdge] = Field(default_factory=list, description="All directional edges in the graph")
    evidence_references: list[str] = Field(
        default_factory=list, description="List of all real evidence IDs referenced in this graph"
    )
    evidence_details: list[dict[str, Any]] = Field(
        default_factory=list, description="Detailed records of referenced evidence with availability status"
    )
    created_at: str
    updated_at: str
    is_cached: bool = Field(default=False, description="Whether this graph was loaded from persistence")


# -------------------------------------------------------------------
# Graph Construction Engine
# -------------------------------------------------------------------


async def build_incident_causal_graph(
    incident_id: str,
    force_refresh: bool = False,
    persist: bool = True,
) -> CausalGraphResponse:
    """Construct an incident-specific causal graph using actual incident telemetry,
    correlated operational events, service dependencies, and root-cause analysis.
    
    Adheres strictly to non-fabrication and non-causation rules:
    - Never creates causal edges based on temporal proximity alone.
    - Preserves verified vs configured vs inferred distinctions.
    - If evidence is insufficient, outputs topology without causal edges.
    """
    ensure_initialized()

    incident = get_incident(incident_id)
    if not incident:
        raise KeyError(f"Incident {incident_id!r} not found")

    # If cached graph exists and refresh not requested, return saved graph
    if not force_refresh:
        cached = get_investigation_graph(incident_id)
        if cached:
            try:
                def normalize_cached_graph(value: Any) -> Any:
                    if isinstance(value, datetime):
                        if value.tzinfo is None:
                            value = value.replace(tzinfo=timezone.utc)
                        return value.isoformat()
                    if isinstance(value, dict):
                        return {key: normalize_cached_graph(item) for key, item in value.items()}
                    if isinstance(value, (list, tuple)):
                        return [normalize_cached_graph(item) for item in value]
                    return value

                resp = CausalGraphResponse(**normalize_cached_graph(cached))
                resp.is_cached = True
                return resp
            except Exception as exc:
                logger.warning("Could not deserialize saved investigation graph: %s", exc)

    incident_service = incident.get("service") or "unknown"
    incident_title = incident.get("title") or "Untitled Incident"

    def iso_timestamp(value: Any) -> str | None:
        """Convert Firestore datetime values to the API's ISO-string contract."""
        if value is None:
            return None
        if isinstance(value, datetime):
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            return value.isoformat()
        if hasattr(value, "isoformat") and callable(value.isoformat):
            try:
                return value.isoformat()
            except (TypeError, ValueError):
                pass
        return str(value)

    now_iso = datetime.now(timezone.utc).isoformat()

    # 1. Gather all operational data and analyses
    correlated_resp = correlate_events_for_incident(incident_id, persist=True)
    raw_events = list_operational_events(incident_id)
    service_deps = get_service_dependencies(incident_service)
    rca = await perform_root_cause_analysis(incident_id, force_refresh=force_refresh, persist=True)

    nodes_map: dict[str, CausalGraphNode] = {}
    edges_map: dict[str, CausalGraphEdge] = {}
    evidence_refs: set[str] = set()
    seen_entity_keys: set[tuple[str, str]] = set()
    seen_edge_triplets: set[tuple[str, str, str]] = set()

    # Helper to add node safely and deduplicate visual nodes by stable entity ID
    def add_node(node: CausalGraphNode) -> None:
        if node.id in nodes_map:
            return
        entity_key = (node.node_type, node.entity_id)
        if node.entity_id and entity_key in seen_entity_keys:
            return
        if node.entity_id:
            seen_entity_keys.add(entity_key)
        nodes_map[node.id] = node

    # Helper to add edge safely, ensuring both endpoints exist and avoiding duplicate edges
    def add_edge(edge: CausalGraphEdge) -> None:
        if edge.id in edges_map:
            return
        if edge.source not in nodes_map or edge.target not in nodes_map:
            return
        triplet = (edge.source, edge.target, edge.relationship_type)
        if triplet in seen_edge_triplets:
            return
        seen_edge_triplets.add(triplet)
        edges_map[edge.id] = edge

    # 2. Add Primary Incident Service Node
    primary_svc_node_id = f"node_svc_{incident_service}"
    add_node(
        CausalGraphNode(
            id=primary_svc_node_id,
            entity_id=incident_service,
            label=f"Service: {incident_service}",
            node_type="service",
            service=incident_service,
            timestamp=iso_timestamp(incident.get("created_at")),
            metadata={
                "is_incident_service": True,
                "environment": incident.get("environment") or "production",
                "severity": incident.get("severity") or "SEV2",
                "status": incident.get("status") or "open",
            },
        )
    )

    # 3. Add Upstream and Downstream Service Dependency Nodes & Edges
    upstream_list = service_deps.get("upstream", [])
    for dep in upstream_list:
        target_svc = dep.get("target") or dep.get("target_service")
        if not target_svc:
            continue
        target_node_id = f"node_svc_{target_svc}"
        add_node(
            CausalGraphNode(
                id=target_node_id,
                entity_id=target_svc,
                label=f"Upstream: {target_svc}",
                node_type="service",
                service=target_svc,
                metadata={
                    "is_upstream": True,
                    "service_type": dep.get("relationship_type") or "service",
                    "verification_status": dep.get("verification_status") or "configured",
                },
            )
        )
        edge_id = f"edge_dep_{incident_service}_{target_svc}"
        add_edge(
            CausalGraphEdge(
                id=edge_id,
                source=primary_svc_node_id,
                target=target_node_id,
                relationship_type=dep.get("relationship_type") or "depends_on",
                explanation=dep.get("evidence") or f"{incident_service} depends on {target_svc}",
                supporting_evidence_ids=[],
                verification_status=dep.get("verification_status") or "configured",
                confidence=dep.get("confidence") or "medium",
            )
        )

    downstream_list = service_deps.get("downstream", [])
    for dep in downstream_list:
        source_svc = dep.get("source") or dep.get("source_service")
        if not source_svc or source_svc == incident_service:
            continue
        source_node_id = f"node_svc_{source_svc}"
        add_node(
            CausalGraphNode(
                id=source_node_id,
                entity_id=source_svc,
                label=f"Downstream: {source_svc}",
                node_type="service",
                service=source_svc,
                metadata={
                    "is_downstream": True,
                    "verification_status": dep.get("verification_status") or "configured",
                },
            )
        )
        edge_id = f"edge_dep_{source_svc}_{incident_service}"
        add_edge(
            CausalGraphEdge(
                id=edge_id,
                source=source_node_id,
                target=primary_svc_node_id,
                relationship_type=dep.get("relationship_type") or "calls_service",
                explanation=f"Downstream caller {source_svc} depends on {incident_service}",
                supporting_evidence_ids=[],
                verification_status=dep.get("verification_status") or "configured",
                confidence=dep.get("confidence") or "medium",
            )
        )

    # 4. Add Operational Events Nodes (Deployments, Errors, Health)
    for cev in correlated_resp.events:
        eid = cev.event_id
        if not eid:
            continue
        evidence_refs.add(eid)

        svc_node_id = f"node_svc_{cev.service}"
        # Ensure service node exists even if external/unregistered
        if svc_node_id not in nodes_map:
            add_node(
                CausalGraphNode(
                    id=svc_node_id,
                    entity_id=cev.service,
                    label=f"Service: {cev.service}",
                    node_type="service",
                    service=cev.service,
                    metadata={"is_external": True},
                )
            )

        if cev.event_type == "deployment":
            dep_node_id = f"node_dep_{eid}"
            add_node(
                CausalGraphNode(
                    id=dep_node_id,
                    entity_id=eid,
                    label=f"Deploy: {cev.revision or cev.service}",
                    node_type="deployment",
                    service=cev.service,
                    timestamp=iso_timestamp(cev.timestamp),
                    evidence_ids=[eid],
                    metadata={
                        "revision": cev.revision,
                        "description": cev.description,
                        "severity": cev.severity,
                        "source": cev.evidence_source,
                    },
                )
            )
            # Structural edge: deployment occurred on this service
            add_edge(
                CausalGraphEdge(
                    id=f"edge_deployed_{eid}",
                    source=dep_node_id,
                    target=svc_node_id,
                    relationship_type="deployed_to",
                    explanation=f"Revision {cev.revision or 'latest'} deployed to {cev.service} at {cev.timestamp}",
                    supporting_evidence_ids=[eid],
                    verification_status="verified",
                    confidence="Confirmed",
                )
            )

        elif cev.event_type == "error_log":
            err_node_id = f"node_err_{eid}"
            add_node(
                CausalGraphNode(
                    id=err_node_id,
                    entity_id=eid,
                    label=f"Error: {cev.description[:32]}...",
                    node_type="error_event",
                    service=cev.service,
                    timestamp=iso_timestamp(cev.timestamp),
                    evidence_ids=[eid],
                    metadata={
                        "severity": cev.severity or "ERROR",
                        "description": cev.description,
                        "classification": cev.classification.value,
                        "source": cev.evidence_source,
                    },
                )
            )
            # Structural edge: error emitted by service
            is_temporal = cev.classification == CorrelationClassification.TEMPORALLY_ASSOCIATED
            add_edge(
                CausalGraphEdge(
                    id=f"edge_emitted_{eid}",
                    source=err_node_id,
                    target=svc_node_id,
                    relationship_type="occurred_on",
                    explanation=(
                        f"Error log emitted by {cev.service} at {cev.timestamp}"
                        if not is_temporal
                        else f"Operational event on {cev.service} at {cev.timestamp}; strictly temporal association without proven causation."
                    ),
                    supporting_evidence_ids=[eid],
                    verification_status="verified",
                    confidence="Confirmed" if not is_temporal else "Low",
                )
            )

        elif cev.event_type == "health_check":
            health_node_id = f"node_health_{eid}"
            add_node(
                CausalGraphNode(
                    id=health_node_id,
                    entity_id=eid,
                    label=f"Health: {cev.severity or 'CHECK'}",
                    node_type="health_event",
                    service=cev.service,
                    timestamp=iso_timestamp(cev.timestamp),
                    evidence_ids=[eid],
                    metadata={
                        "severity": cev.severity,
                        "description": cev.description,
                        "source": cev.evidence_source,
                    },
                )
            )
            add_edge(
                CausalGraphEdge(
                    id=f"edge_checked_{eid}",
                    source=health_node_id,
                    target=svc_node_id,
                    relationship_type="checked_service",
                    explanation=f"Health check telemetry on {cev.service}: {cev.description}",
                    supporting_evidence_ids=[eid],
                    verification_status="verified",
                    confidence="Confirmed",
                )
            )

    # 5. Add Root-Cause Hypotheses & Evidence-Backed Causal Edges
    # STRICT RULE: Never create a causal edge if evidence is insufficient or purely temporal!
    leading_hyp_dict: dict[str, Any] | None = None

    if not rca.insufficient_evidence and rca.hypotheses:
        leading_hyp = rca.hypotheses[0]
        leading_hyp_dict = {
            "rank": leading_hyp.rank,
            "suspected_service": leading_hyp.suspected_service,
            "suspected_component": leading_hyp.suspected_component,
            "confidence": leading_hyp.confidence,
            "explanation": leading_hyp.explanation,
        }

        for hyp in rca.hypotheses:
            hyp_node_id = f"node_hyp_{hyp.rank}"
            add_node(
                CausalGraphNode(
                    id=hyp_node_id,
                    entity_id=f"hyp_{hyp.rank}",
                    label=f"#{hyp.rank} Cause: {hyp.suspected_service}",
                    node_type="root_cause_hypothesis",
                    service=hyp.suspected_service,
                    evidence_ids=hyp.supporting_evidence_ids,
                    is_leading_hypothesis=(hyp.rank == 1),
                    metadata={
                        "rank": hyp.rank,
                        "confidence": hyp.confidence,
                        "suspected_service": hyp.suspected_service,
                        "suspected_component": hyp.suspected_component,
                        "explanation": hyp.explanation,
                        "confidence_explanation": hyp.confidence_explanation,
                        "is_leading": hyp.rank == 1,
                        "ranking_rationale": hyp.ranking_rationale,
                    },
                )
            )

            # Inferred causal link: Hypothesis -> Suspected Service
            target_svc_node = f"node_svc_{hyp.suspected_service}"
            if target_svc_node in nodes_map:
                add_edge(
                    CausalGraphEdge(
                        id=f"edge_hyp_cause_{hyp.rank}_{hyp.suspected_service}",
                        source=hyp_node_id,
                        target=target_svc_node,
                        relationship_type="hypothesized_cause",
                        explanation=hyp.explanation,
                        supporting_evidence_ids=hyp.supporting_evidence_ids,
                        verification_status="inferred",
                        confidence=hyp.confidence,
                    )
                )

            # Inferred causal links: Hypothesis -> Corroborating Error Events (causes)
            for eid in hyp.supporting_evidence_ids:
                evidence_refs.add(eid)
                err_node_id = f"node_err_{eid}"
                dep_node_id = f"node_dep_{eid}"

                if err_node_id in nodes_map:
                    add_edge(
                        CausalGraphEdge(
                            id=f"edge_causes_{hyp.rank}_{eid}",
                            source=hyp_node_id,
                            target=err_node_id,
                            relationship_type="causes",
                            explanation=f"Hypothesis #{hyp.rank} ({hyp.suspected_service}) directly caused error {eid}",
                            supporting_evidence_ids=[eid],
                            verification_status="inferred",
                            confidence=hyp.confidence,
                        )
                    )

                if dep_node_id in nodes_map:
                    add_edge(
                        CausalGraphEdge(
                            id=f"edge_ev_for_{dep_node_id}_{hyp.rank}",
                            source=dep_node_id,
                            target=hyp_node_id,
                            relationship_type="evidence_for",
                            explanation=f"Deployment event {eid} constitutes primary evidence for hypothesis #{hyp.rank}",
                            supporting_evidence_ids=[eid],
                            verification_status="verified",
                            confidence="Confirmed",
                        )
                    )

            # Inferred causal link: Upstream Service Failure -> Incident Service (propagates_to)
            if hyp.suspected_service.lower() != incident_service.lower():
                upstream_svc_node = f"node_svc_{hyp.suspected_service}"
                if upstream_svc_node in nodes_map:
                    add_edge(
                        CausalGraphEdge(
                            id=f"edge_propagates_{hyp.suspected_service}_{incident_service}",
                            source=upstream_svc_node,
                            target=primary_svc_node_id,
                            relationship_type="propagates_to",
                            explanation=(
                                f"Failure in upstream dependency {hyp.suspected_service} cascades to {incident_service}"
                            ),
                            supporting_evidence_ids=hyp.supporting_evidence_ids,
                            verification_status="inferred",
                            confidence=hyp.confidence,
                        )
                    )

            # Inferred causal link: Deployment -> Error on primary service (triggers_error)
            for eid in hyp.supporting_evidence_ids:
                dep_node_id = f"node_dep_{eid}"
                if dep_node_id in nodes_map:
                    # Connect to direct error events
                    for err_eid in hyp.supporting_evidence_ids:
                        err_node_id = f"node_err_{err_eid}"
                        if err_node_id in nodes_map and err_node_id != dep_node_id:
                            add_edge(
                                CausalGraphEdge(
                                    id=f"edge_triggers_{eid}_{err_eid}",
                                    source=dep_node_id,
                                    target=err_node_id,
                                    relationship_type="triggers_error",
                                    explanation=f"Deployment {eid} applied configuration/code triggering error {err_eid}",
                                    supporting_evidence_ids=[eid, err_eid],
                                    verification_status="inferred",
                                    confidence=hyp.confidence,
                                )
                            )

    # 6. Build final structured response
    graph_id = f"cgraph_{incident_id}"
    is_insufficient = rca.insufficient_evidence
    leading_hyp_id = "node_hyp_1" if (leading_hyp_dict and "node_hyp_1" in nodes_map) else None

    # Construct rich evidence details with availability flags
    event_lookup: dict[str, Any] = {}
    for rev in raw_events:
        eid = rev.get("event_id") or rev.get("id")
        if eid:
            event_lookup[eid] = rev
    for cev in correlated_resp.events:
        if cev.event_id:
            event_lookup[cev.event_id] = cev

    evidence_details: list[dict[str, Any]] = []
    for eid in sorted(evidence_refs):
        if eid in event_lookup:
            ev = event_lookup[eid]
            etype = getattr(ev, "event_type", None) or (ev.get("event_type") if isinstance(ev, dict) else "unknown")
            esvc = getattr(ev, "service", None) or (ev.get("service") if isinstance(ev, dict) else incident_service)
            ets = getattr(ev, "timestamp", None) or (ev.get("timestamp") if isinstance(ev, dict) else now_iso)
            ets = iso_timestamp(ets)
            edesc = getattr(ev, "description", None) or (ev.get("description") if isinstance(ev, dict) else "")
            epayload = getattr(ev, "payload", None) or (ev.get("payload") if isinstance(ev, dict) else {})
            evidence_details.append({
                "evidence_id": eid,
                "event_type": etype,
                "service": esvc,
                "timestamp": ets,
                "summary": edesc,
                "is_available": True,
                "raw_payload": epayload if isinstance(epayload, dict) else {},
            })
        else:
            evidence_details.append({
                "evidence_id": eid,
                "event_type": "unknown",
                "service": None,
                "timestamp": None,
                "summary": "Evidence record unavailable in store; reference preserved.",
                "is_available": False,
                "raw_payload": {},
            })

    summary = (
        "Insufficient evidence to establish causal edges. Displaying service topology and operational events."
        if is_insufficient
        else f"Causal investigation graph with {len(nodes_map)} nodes and {len(edges_map)} relationships. "
        f"Leading cause: {leading_hyp_dict['explanation'] if leading_hyp_dict else 'Under investigation'}."
    )

    response = CausalGraphResponse(
        graph_id=graph_id,
        incident_id=incident_id,
        incident_service=incident_service,
        incident_title=incident_title,
        schema_version="1.0",
        status="insufficient_evidence" if is_insufficient else "sufficient_evidence",
        insufficient_evidence=is_insufficient,
        summary=summary,
        analysis_mode=rca.analysis_mode,
        leading_hypothesis=leading_hyp_dict,
        leading_hypothesis_id=leading_hyp_id,
        nodes=list(nodes_map.values()),
        edges=list(edges_map.values()),
        evidence_references=sorted(evidence_refs),
        evidence_details=evidence_details,
        created_at=now_iso,
        updated_at=now_iso,
        is_cached=False,
    )

    # 7. Persist graph if requested
    if persist:
        try:
            save_investigation_graph(incident_id, response.model_dump())
        except Exception as exc:
            logger.exception("Failed to persist investigation graph for incident %s: %s", incident_id, exc)

    return response
