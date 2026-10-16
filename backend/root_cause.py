"""Evidence-based root-cause analysis engine for Tracewell Incident Copilot.

Generates ranked, evidence-backed root-cause hypotheses using incident details,
operational events, correlated evidence, service dependencies, and investigation findings.

STRICT PRINCIPLES:
1. Never invent events, deployment changes, logs, metrics, or evidence IDs.
2. Validate that every cited evidence ID exists in the supplied evidence set.
3. Timestamp proximity alone must NEVER establish causation.
4. Distinguish observed facts, inferred relationships, and unverified assumptions.
5. Never claim a root cause is confirmed unless sufficient direct evidence exists.
6. If evidence is insufficient, explicitly return "Insufficient evidence to determine root cause."
7. Configured dependencies must not be presented as independently verified.
8. Do not present model-generated confidence as a statistically calibrated probability.
9. Keep ranking explainable; do not rely on an unexplained arbitrary score.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from .config import (
    GCP_PROJECT,
    GCP_REGION,
    GEMINI_MODEL,
    LOCAL_FALLBACK,
    USE_VERTEX_AI,
)
from .correlation import (
    CorrelatedEvent,
    CorrelationClassification,
    correlate_events_for_incident,
)
from .dependencies import (
    ensure_initialized,
    get_service_dependencies,
)
from .store import (
    get_incident,
    get_root_cause_analysis,
    list_operational_events,
    save_root_cause_analysis,
    server_timestamp,
    update_incident,
)

logger = logging.getLogger("tracewell.root_cause")


# -------------------------------------------------------------------
# Pydantic Data Models
# -------------------------------------------------------------------


class ConfidenceLevel(str, Enum):
    CONFIRMED = "Confirmed"
    PROBABLE = "Probable"
    POSSIBLE = "Possible"


class EvidenceItemRef(BaseModel):
    """Reference to an actual verified evidence item."""
    evidence_id: str = Field(description="Unique identifier of the operational event")
    service: str = Field(description="Service that emitted the event")
    timestamp: str | None = Field(default=None, description="ISO timestamp")
    event_type: str = Field(description="deployment | error_log | health_check | traffic_allocation")
    severity: str | None = Field(default="INFO", description="Severity level")
    source: str = Field(default="Cloud Logging", description="Evidence source")
    description: str = Field(default="", description="Event description")
    relationship_direction: str | None = Field(default=None, description="self | upstream_dependency | downstream_dependent | none")
    verification_status: str | None = Field(default=None, description="verified | configured | none")


class RootCauseHypothesis(BaseModel):
    """A ranked, evidence-backed hypothesis for the incident root cause."""
    rank: int = Field(default=1, description="Ranking among hypotheses (1 is most likely)")
    explanation: str = Field(description="Clear explanation of the hypothesized root cause")
    suspected_service: str = Field(description="Suspected service responsible for the failure")
    suspected_component: str | None = Field(default=None, description="Specific component or configuration")
    confidence: str = Field(description="Qualitative confidence: Confirmed | Probable | Possible")
    confidence_explanation: str = Field(description="Explicit explanation of why this confidence level was assigned")
    supporting_evidence_ids: list[str] = Field(default_factory=list, description="IDs of evidence directly supporting this hypothesis")
    supporting_evidence: list[EvidenceItemRef] = Field(default_factory=list, description="Hydrated evidence items")
    contradicting_evidence: list[str] = Field(default_factory=list, description="Observed evidence that contradicts or weakens this hypothesis")
    observed_facts: list[str] = Field(default_factory=list, description="Directly witnessed facts from telemetry/deployments")
    inferred_relationships: list[str] = Field(default_factory=list, description="Logical deductions made from dependencies or sequence")
    unverified_assumptions: list[str] = Field(default_factory=list, description="Assumptions lacking direct verification")
    recommended_verification_steps: list[str] = Field(default_factory=list, description="Actionable verification steps for ops engineers")
    alternative_explanations: list[str] = Field(default_factory=list, description="Alternative plausible explanations")
    ranking_rationale: str = Field(description="Explainable rationale for this hypothesis's rank")


class RootCauseAnalysisResponse(BaseModel):
    """Structured response containing ranked root-cause hypotheses."""
    incident_id: str
    incident_service: str
    incident_title: str
    status: str = Field(description="sufficient_evidence | insufficient_evidence")
    insufficient_evidence: bool = Field(description="True if evidence is insufficient to determine root cause")
    summary: str = Field(description="Synthesized summary or 'Insufficient evidence to determine root cause.'")
    hypotheses: list[RootCauseHypothesis] = Field(default_factory=list, description="Ranked hypotheses")
    observed_facts: list[str] = Field(default_factory=list, description="Global list of directly observed facts")
    inferred_relationships: list[str] = Field(default_factory=list, description="Global list of inferred relationships")
    unverified_assumptions: list[str] = Field(default_factory=list, description="Global list of unverified assumptions")
    evidence_evaluated_count: int = Field(default=0, description="Total number of evidence items evaluated")
    analysis_mode: str = Field(description="gemini_vertex_ai | deterministic_local_fallback")
    model_name: str = Field(description="Model or engine identifier")
    fallback_reason: str | None = Field(default=None, description="Reason for fallback if applicable")
    created_at: str
    updated_at: str


# -------------------------------------------------------------------
# Evidence Catalog & Validation Helpers
# -------------------------------------------------------------------


def build_evidence_catalog(
    incident_id: str,
    correlated_events: list[CorrelatedEvent],
    raw_events: list[dict[str, Any]],
) -> dict[str, EvidenceItemRef]:
    """Build an indexed catalog of all real, verified operational events.
    
    Guarantees no synthetic or fabricated evidence IDs can enter analysis without detection.
    """
    catalog: dict[str, EvidenceItemRef] = {}

    for cev in correlated_events:
        eid = cev.event_id
        if not eid:
            continue
        catalog[eid] = EvidenceItemRef(
            evidence_id=eid,
            service=cev.service,
            timestamp=cev.timestamp,
            event_type=cev.event_type,
            severity=cev.severity or "INFO",
            source=cev.evidence_source or "Operational Telemetry",
            description=cev.description or "",
            relationship_direction=cev.relationship_direction,
            verification_status=cev.relationship_verification_status,
        )

    for rev in raw_events:
        eid = rev.get("id") or rev.get("event_id")
        if not eid or eid in catalog:
            continue
        catalog[eid] = EvidenceItemRef(
            evidence_id=eid,
            service=rev.get("service", "unknown"),
            timestamp=rev.get("timestamp") or rev.get("event_timestamp"),
            event_type=rev.get("event_type", "event"),
            severity=rev.get("severity") or "INFO",
            source=rev.get("source") or "Operational Telemetry",
            description=rev.get("description", ""),
            relationship_direction=None,
            verification_status=None,
        )

    return catalog


def validate_and_sanitize_evidence_ids(
    cited_ids: list[str],
    catalog: dict[str, EvidenceItemRef],
) -> tuple[list[str], list[EvidenceItemRef], list[str]]:
    """Validate cited evidence IDs against the known catalog.
    
    Returns:
        (valid_ids, hydrated_items, discarded_fabricated_ids)
    """
    valid_ids: list[str] = []
    hydrated: list[EvidenceItemRef] = []
    fabricated: list[str] = []

    for eid in cited_ids:
        if eid in catalog:
            if eid not in valid_ids:
                valid_ids.append(eid)
                hydrated.append(catalog[eid])
        else:
            fabricated.append(eid)
            logger.warning("Detected and rejected non-existent or fabricated evidence ID: %r", eid)

    return valid_ids, hydrated, fabricated


# -------------------------------------------------------------------
# Deterministic Evidence-Based Analysis Engine (Local / Fallback)
# -------------------------------------------------------------------


def analyze_root_cause_deterministic(
    incident: dict[str, Any],
    correlated_events: list[CorrelatedEvent],
    raw_events: list[dict[str, Any]],
    service_deps: dict[str, Any],
    catalog: dict[str, EvidenceItemRef],
) -> RootCauseAnalysisResponse:
    """Deterministic, transparent root-cause analysis based strictly on observable evidence.
    
    Guarantees:
    - Never invents missing evidence.
    - Explicitly distinguishes observed facts vs inferences vs unverified assumptions.
    - Distinguishes upstream causes vs downstream symptoms.
    - If evidence is insufficient, returns "Insufficient evidence to determine root cause."
    """
    incident_id = incident.get("id", "")
    incident_service = incident.get("service") or "unknown"
    incident_title = incident.get("title") or "Untitled Incident"
    now_iso = server_timestamp()

    observed_facts: list[str] = []
    inferred_relationships: list[str] = []
    unverified_assumptions: list[str] = []

    # 1. Tally direct, dependency, and temporal events
    direct_deployments: list[CorrelatedEvent] = []
    direct_errors: list[CorrelatedEvent] = []
    direct_health: list[CorrelatedEvent] = []
    upstream_events: list[CorrelatedEvent] = []
    downstream_events: list[CorrelatedEvent] = []
    temporal_events: list[CorrelatedEvent] = []
    contradicting_signals: list[str] = []

    for cev in correlated_events:
        if cev.classification == CorrelationClassification.DIRECT_MATCH:
            if cev.event_type == "deployment":
                direct_deployments.append(cev)
                observed_facts.append(
                    f"Deployment on {cev.service} ({cev.revision or 'unknown revision'}) at {cev.timestamp}."
                )
            elif cev.event_type == "error_log":
                direct_errors.append(cev)
                observed_facts.append(
                    f"Error log emitted by {cev.service}: '{cev.description[:120]}' at {cev.timestamp}."
                )
            elif cev.event_type == "health_check":
                direct_health.append(cev)
                if cev.severity in ("ERROR", "CRITICAL"):
                    observed_facts.append(f"Health check failure on {cev.service} at {cev.timestamp}.")
                else:
                    observed_facts.append(f"Successful health check recorded for {cev.service} at {cev.timestamp}.")
                    contradicting_signals.append(
                        f"Health check on {cev.service} reported normal status at {cev.timestamp}."
                    )
        elif cev.classification == CorrelationClassification.DEPENDENCY_RELATED:
            if cev.relationship_direction == "upstream_dependency":
                upstream_events.append(cev)
                status_label = "verified" if cev.relationship_verification_status == "verified" else "configured (unverified)"
                observed_facts.append(
                    f"Upstream dependency {cev.service} emitted {cev.event_type} at {cev.timestamp}."
                )
                if cev.relationship_verification_status != "verified":
                    unverified_assumptions.append(
                        f"Dependency from {incident_service} to {cev.service} is configured in specification but not independently verified at runtime."
                    )
            elif cev.relationship_direction == "downstream_dependent":
                downstream_events.append(cev)
                observed_facts.append(
                    f"Downstream dependent {cev.service} experienced cascading errors at {cev.timestamp}."
                )
                inferred_relationships.append(
                    f"Errors on downstream dependent {cev.service} are cascading blast-radius symptoms, not the root cause."
                )
        elif cev.classification == CorrelationClassification.TEMPORALLY_ASSOCIATED:
            temporal_events.append(cev)
            unverified_assumptions.append(
                f"Operational event on unrelated service {cev.service} at {cev.timestamp} occurred near the incident window; timestamp proximity alone does not establish causation."
            )

    # 2. Check for Insufficient Evidence
    # If no direct errors, no deployments, and no upstream events exist:
    has_causal_evidence = bool(direct_deployments or direct_errors or upstream_events)

    if not has_causal_evidence:
        summary_text = "Insufficient evidence to determine root cause."
        if not correlated_events:
            observed_facts.append("No operational events (deployments, errors, or health events) are recorded for this incident.")
        else:
            observed_facts.append(
                f"Evaluated {len(correlated_events)} operational event(s), but none represent a direct error, deployment change, or upstream failure on {incident_service}."
            )
        unverified_assumptions.append("Missing live Cloud Run revision metadata and Cloud Logging telemetry.")

        return RootCauseAnalysisResponse(
            incident_id=incident_id,
            incident_service=incident_service,
            incident_title=incident_title,
            status="insufficient_evidence",
            insufficient_evidence=True,
            summary=summary_text,
            hypotheses=[],
            observed_facts=observed_facts,
            inferred_relationships=inferred_relationships,
            unverified_assumptions=unverified_assumptions,
            evidence_evaluated_count=len(catalog),
            analysis_mode="deterministic_local_fallback",
            model_name="tracewell_deterministic_engine_v1",
            created_at=now_iso,
            updated_at=now_iso,
        )

    # 3. Generate Ranked Hypotheses
    # Prioritize most recent events closest to the incident window
    direct_deployments.sort(key=lambda e: str(e.timestamp or ""), reverse=True)
    direct_errors.sort(key=lambda e: str(e.timestamp or ""), reverse=True)
    upstream_events.sort(key=lambda e: str(e.timestamp or ""), reverse=True)

    hypotheses: list[RootCauseHypothesis] = []

    # Hypothesis A: Direct Deployment / Configuration Error on affected service
    if direct_deployments and direct_errors:
        latest_dep = direct_deployments[0]
        supp_ids = [latest_dep.event_id] + [e.event_id for e in direct_errors[:4]]
        valid_ids, hydrated_items, _ = validate_and_sanitize_evidence_ids(supp_ids, catalog)

        # Check for specific configuration indicators (e.g., DATABASE_URL, timeout)
        desc_combined = " ".join([latest_dep.description] + [e.description for e in direct_errors]).lower()
        if "database_url" in desc_combined or "database" in desc_combined or "db" in desc_combined:
            component = "DATABASE_URL / Database connection configuration"
            explanation = (
                f"A recent deployment ({latest_dep.revision or 'latest revision'}) to {incident_service} "
                f"introduced an invalid or unreachable database configuration, causing subsequent connection failures."
            )
        elif "config" in desc_combined or "env" in desc_combined:
            component = "Environment variable / Service configuration"
            explanation = (
                f"Deployment of revision {latest_dep.revision or 'latest'} on {incident_service} "
                f"applied a misconfigured environment variable, leading to runtime failures."
            )
        else:
            component = f"Revision {latest_dep.revision or 'deployment'}"
            explanation = (
                f"Recent revision deployment ({latest_dep.revision or 'unspecified'}) on {incident_service} "
                f"coincided with an onset of application errors."
            )

        inferred_relationships.append(
            f"Errors on {incident_service} began following the deployment of revision {latest_dep.revision or 'latest'}."
        )

        has_contradiction = bool(contradicting_signals)
        confidence = (
            ConfidenceLevel.CONFIRMED.value
            if (len(direct_errors) >= 2 and not has_contradiction and latest_dep.revision)
            else ConfidenceLevel.PROBABLE.value
        )
        conf_expl = (
            f"Assigned '{confidence}' because direct deployment evidence ({latest_dep.event_id}) "
            f"is corroborated by {len(direct_errors)} direct error log(s) on the affected service."
            + (" However, conflicting healthy signals were observed." if has_contradiction else "")
        )

        hypotheses.append(
            RootCauseHypothesis(
                rank=1,
                explanation=explanation,
                suspected_service=incident_service,
                suspected_component=component,
                confidence=confidence,
                confidence_explanation=conf_expl,
                supporting_evidence_ids=valid_ids,
                supporting_evidence=hydrated_items,
                contradicting_evidence=contradicting_signals,
                observed_facts=[
                    f"Deployment {latest_dep.event_id} occurred at {latest_dep.timestamp}.",
                    f"{len(direct_errors)} error logs recorded directly on {incident_service}.",
                ],
                inferred_relationships=[
                    f"The deployed revision {latest_dep.revision or 'latest'} directly caused the observed {direct_errors[0].event_type} failures."
                ],
                unverified_assumptions=[
                    "Assumes deployment traffic allocation was shifted to the new revision without a canary rollback."
                ],
                recommended_verification_steps=[
                    f"Inspect environment variables and secret bindings for revision {latest_dep.revision or 'latest'} on {incident_service}.",
                    f"Compare diff between revision {latest_dep.revision or 'latest'} and the previous known-healthy revision.",
                    "Review database credentials and network egress firewall rules.",
                ],
                alternative_explanations=[
                    "Upstream database server outage or network partition coinciding with deployment."
                ],
                ranking_rationale=(
                    f"Rank #1: Strongest direct causal evidence on primary service {incident_service} "
                    f"with {len(valid_ids)} direct corroborating events."
                ),
            )
        )

    # Hypothesis B: Direct Error Spike without deployment
    elif direct_errors and not direct_deployments:
        supp_ids = [e.event_id for e in direct_errors[:5]]
        valid_ids, hydrated_items, _ = validate_and_sanitize_evidence_ids(supp_ids, catalog)

        explanation = (
            f"Direct operational errors observed on {incident_service} indicating internal service degradation "
            f"or resource exhaustion without a preceding deployment event."
        )
        hypotheses.append(
            RootCauseHypothesis(
                rank=1,
                explanation=explanation,
                suspected_service=incident_service,
                suspected_component="Runtime process / Internal state",
                confidence=ConfidenceLevel.PROBABLE.value if len(direct_errors) > 1 else ConfidenceLevel.POSSIBLE.value,
                confidence_explanation=(
                    f"Direct error logs ({len(direct_errors)}) confirm failure on {incident_service}, "
                    f"but lack of deployment records limits root cause attribution."
                ),
                supporting_evidence_ids=valid_ids,
                supporting_evidence=hydrated_items,
                contradicting_evidence=contradicting_signals,
                observed_facts=[
                    f"Error log: '{direct_errors[0].description[:140]}' on {incident_service}."
                ],
                inferred_relationships=[
                    f"Service {incident_service} is unable to process incoming requests successfully."
                ],
                unverified_assumptions=[
                    "Assumes no unlogged deployment or external infrastructure change occurred."
                ],
                recommended_verification_steps=[
                    f"Check CPU, memory, and container instance metrics for {incident_service}.",
                    f"Check Cloud Logging for unhandled exceptions or panic stack traces.",
                ],
                alternative_explanations=[
                    "External dependency timeout or sudden traffic spike exhausting worker threads."
                ],
                ranking_rationale="Rank #1: Direct error telemetry observed on the affected service.",
            )
        )

    # Hypothesis C: Upstream Dependency Failure (e.g. database, auth)
    if upstream_events:
        for uev in upstream_events[:2]:
            supp_ids = [uev.event_id] + [e.event_id for e in direct_errors[:2]]
            valid_ids, hydrated_items, _ = validate_and_sanitize_evidence_ids(supp_ids, catalog)

            is_verified = uev.relationship_verification_status == "verified"
            confidence = ConfidenceLevel.PROBABLE.value if is_verified else ConfidenceLevel.POSSIBLE.value
            verification_note = (
                "Verified dependency relationship."
                if is_verified
                else "Warning: Configured dependency relationship is not independently verified."
            )

            explanation = (
                f"Degradation or failure in upstream dependency '{uev.service}' propagated to {incident_service}, "
                f"impairing its ability to fulfill requests."
            )

            hyp_rank = len(hypotheses) + 1
            hypotheses.append(
                RootCauseHypothesis(
                    rank=hyp_rank,
                    explanation=explanation,
                    suspected_service=uev.service,
                    suspected_component=f"Upstream dependency ({uev.relationship_type or 'service'})",
                    confidence=confidence,
                    confidence_explanation=(
                        f"Assigned '{confidence}' because upstream service {uev.service} experienced failures. {verification_note}"
                    ),
                    supporting_evidence_ids=valid_ids,
                    supporting_evidence=hydrated_items,
                    contradicting_evidence=[],
                    observed_facts=[
                        f"Upstream service {uev.service} emitted {uev.event_type} at {uev.timestamp}."
                    ],
                    inferred_relationships=[
                        f"{incident_service} depends on {uev.service}; failure in {uev.service} cascades to {incident_service}."
                    ],
                    unverified_assumptions=[
                        f"Assumes connection from {incident_service} to {uev.service} was actively attempted during the failure window."
                        if not is_verified
                        else "Assumes upstream failure directly correlates with downstream timeout thresholds."
                    ],
                    recommended_verification_steps=[
                        f"Inspect health metrics and error logs for upstream dependency {uev.service}.",
                        f"Verify network connectivity and latency between {incident_service} and {uev.service}.",
                    ],
                    alternative_explanations=[
                        f"Transient network glitch or client-side retry storm towards {uev.service}."
                    ],
                    ranking_rationale=(
                        f"Rank #{hyp_rank}: Upstream dependency {uev.service} failure. "
                        f"Ranked below direct matches because direct service errors have higher causal directness."
                    ),
                )
            )

    # Re-rank hypotheses based on explainable criteria:
    # 1. Category: Direct deployment+error (0) > Direct error only (1) > Upstream verified dependency (2) > Upstream configured dependency (3) > Other (4)
    # 2. Confidence: Confirmed (0) > Probable (1) > Possible (2)
    # 3. Contradicting evidence count: 0 contradictions ranks before > 0 contradictions
    def _rank_key(h: RootCauseHypothesis) -> tuple[int, int, int]:
        is_direct_causal = "direct causal" in h.ranking_rationale.lower()
        is_direct_error = "direct error" in h.ranking_rationale.lower()
        is_verified_dep = "verified dependency relationship" in h.confidence_explanation.lower()
        is_configured_dep = "configured dependency relationship" in h.confidence_explanation.lower()

        if is_direct_causal:
            cat = 0
        elif is_direct_error:
            cat = 1
        elif is_verified_dep:
            cat = 2
        elif is_configured_dep:
            cat = 3
        else:
            cat = 4

        conf = 0 if h.confidence == "Confirmed" else 1 if h.confidence == "Probable" else 2
        contra = len(h.contradicting_evidence)
        return (cat, conf, contra)

    hypotheses.sort(key=_rank_key)

    for idx, hyp in enumerate(hypotheses, 1):
        hyp.rank = idx
        if "direct causal" in hyp.ranking_rationale.lower():
            hyp.ranking_rationale = (
                f"Rank #{idx}: Highest causal directness on primary service {hyp.suspected_service} "
                f"with {len(hyp.supporting_evidence_ids)} direct corroborating events and {len(hyp.contradicting_evidence)} contradiction(s)."
            )
        elif "direct error" in hyp.ranking_rationale.lower():
            hyp.ranking_rationale = (
                f"Rank #{idx}: Direct error telemetry observed on primary service {hyp.suspected_service}."
            )
        elif "verified dependency relationship" in hyp.confidence_explanation.lower():
            hyp.ranking_rationale = (
                f"Rank #{idx}: Upstream failure in verified dependency '{hyp.suspected_service}'. "
                f"Ranked above unverified/configured dependencies."
            )
        elif "configured dependency relationship" in hyp.confidence_explanation.lower():
            hyp.ranking_rationale = (
                f"Rank #{idx}: Upstream failure in configured dependency '{hyp.suspected_service}'. "
                f"Ranked below verified dependencies because this link is not independently verified."
            )
        else:
            hyp.ranking_rationale = f"Rank #{idx}: Hypothesis based on observable telemetry."

    if not hypotheses:
        return RootCauseAnalysisResponse(
            incident_id=incident_id,
            incident_service=incident_service,
            incident_title=incident_title,
            status="insufficient_evidence",
            insufficient_evidence=True,
            summary="Insufficient evidence to determine root cause.",
            hypotheses=[],
            observed_facts=observed_facts,
            inferred_relationships=inferred_relationships,
            unverified_assumptions=unverified_assumptions,
            evidence_evaluated_count=len(catalog),
            analysis_mode="deterministic_local_fallback",
            model_name="tracewell_deterministic_engine_v1",
            created_at=now_iso,
            updated_at=now_iso,
        )

    summary = (
        f"Identified {len(hypotheses)} evidence-backed root-cause hypothesis(es). "
        f"Top suspected cause: {hypotheses[0].explanation}"
    )

    return RootCauseAnalysisResponse(
        incident_id=incident_id,
        incident_service=incident_service,
        incident_title=incident_title,
        status="sufficient_evidence",
        insufficient_evidence=False,
        summary=summary,
        hypotheses=hypotheses,
        observed_facts=observed_facts,
        inferred_relationships=inferred_relationships,
        unverified_assumptions=unverified_assumptions,
        evidence_evaluated_count=len(catalog),
        analysis_mode="deterministic_local_fallback",
        model_name="tracewell_deterministic_engine_v1",
        created_at=now_iso,
        updated_at=now_iso,
    )


# -------------------------------------------------------------------
# Vertex AI / Gemini 2.5 Flash Structured Analysis Engine
# -------------------------------------------------------------------


async def analyze_root_cause_with_gemini(
    incident: dict[str, Any],
    correlated_events: list[CorrelatedEvent],
    raw_events: list[dict[str, Any]],
    service_deps: dict[str, Any],
    catalog: dict[str, EvidenceItemRef],
) -> RootCauseAnalysisResponse:
    """Use Gemini 2.5 Flash via Vertex AI to perform structured root-cause analysis.
    
    Strictly validates that any cited evidence ID exists in catalog.
    If Gemini fails, throws to allow clean fallback.
    """
    from google.genai import Client, types

    incident_id = incident.get("id", "")
    incident_service = incident.get("service") or "unknown"
    incident_title = incident.get("title") or "Untitled Incident"
    now_iso = server_timestamp()

    # Prepare sanitized evidence payload (no bloat, strictly indexed by ID)
    evidence_payload = {
        eid: {
            "evidence_id": item.evidence_id,
            "service": item.service,
            "timestamp": item.timestamp,
            "event_type": item.event_type,
            "severity": item.severity,
            "description": item.description,
            "relationship_direction": item.relationship_direction,
            "verification_status": item.verification_status,
        }
        for eid, item in catalog.items()
    }

    dependencies_payload = {
        "service": incident_service,
        "upstream": service_deps.get("upstream", []),
        "downstream": service_deps.get("downstream", []),
    }

    prompt = f"""You are Tracewell's Evidence-Based Root Cause Analyst.
Analyze the following production incident and operational evidence.

INCIDENT CONTEXT:
ID: {incident_id}
Service: {incident_service}
Title: {incident_title}
Description: {incident.get('description', '')}
Severity: {incident.get('severity', '')}

AVAILABLE EVIDENCE CATALOG (Indexed by evidence_id):
{json.dumps(evidence_payload, indent=2)}

SERVICE DEPENDENCIES:
{json.dumps(dependencies_payload, indent=2)}

STRICT RULES:
1. NEVER INVENT evidence IDs, deployment changes, logs, or metrics. Every cited ID in `supporting_evidence_ids` MUST exist in the catalog above.
2. Timestamp proximity alone must NEVER establish causation.
3. Distinguish observed facts, inferred relationships, and unverified assumptions.
4. If evidence is insufficient to identify a cause, return status='insufficient_evidence', insufficient_evidence=true, and summary='Insufficient evidence to determine root cause.'
5. Configured dependencies must NOT be presented as independently verified.
6. Do NOT present confidence as a numerical probability. Use 'Confirmed', 'Probable', or 'Possible'.
7. For each hypothesis, explain ranking rationale clearly.

Return structured JSON conforming to the requested schema.
"""

    client = Client(
        vertexai=USE_VERTEX_AI,
        project=GCP_PROJECT,
        location=GCP_REGION,
    )

    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=RootCauseAnalysisResponse,
            temperature=0.1,
            system_instruction=(
                "You are an expert site reliability engineer conducting strict evidence-based incident analysis. "
                "You prioritize direct telemetry and verified dependencies over speculation."
            ),
        ),
    )

    raw_text = response.text or "{}"
    parsed_json = json.loads(raw_text)

    # Validate and sanitize all cited evidence IDs against the real catalog
    sanitized_hypotheses: list[RootCauseHypothesis] = []
    for h_data in parsed_json.get("hypotheses", []):
        raw_ids = h_data.get("supporting_evidence_ids", [])
        valid_ids, hydrated, discarded = validate_and_sanitize_evidence_ids(raw_ids, catalog)
        
        # If model hallucinated evidence IDs and has no valid evidence left, downgrade or discard
        if not valid_ids:
            logger.warning(
                "Gemini hypothesis for service %r lacked valid evidence IDs (discarded: %r)",
                h_data.get("suspected_service"),
                discarded,
            )
            continue

        h_data["supporting_evidence_ids"] = valid_ids
        h_data["supporting_evidence"] = [item.model_dump() for item in hydrated]
        sanitized_hypotheses.append(RootCauseHypothesis(**h_data))

    is_insufficient = parsed_json.get("insufficient_evidence", False) or len(sanitized_hypotheses) == 0

    return RootCauseAnalysisResponse(
        incident_id=incident_id,
        incident_service=incident_service,
        incident_title=incident_title,
        status="insufficient_evidence" if is_insufficient else "sufficient_evidence",
        insufficient_evidence=is_insufficient,
        summary=(
            "Insufficient evidence to determine root cause."
            if is_insufficient
            else parsed_json.get("summary", "Analysis completed.")
        ),
        hypotheses=sanitized_hypotheses,
        observed_facts=parsed_json.get("observed_facts", []),
        inferred_relationships=parsed_json.get("inferred_relationships", []),
        unverified_assumptions=parsed_json.get("unverified_assumptions", []),
        evidence_evaluated_count=len(catalog),
        analysis_mode="gemini_vertex_ai",
        model_name=GEMINI_MODEL,
        created_at=now_iso,
        updated_at=now_iso,
    )


# -------------------------------------------------------------------
# Main Root Cause Analysis Orchestrator
# -------------------------------------------------------------------


async def perform_root_cause_analysis(
    incident_id: str,
    force_refresh: bool = False,
    persist: bool = True,
) -> RootCauseAnalysisResponse:
    """Perform evidence-based root-cause analysis for an incident.
    
    Tries Vertex AI / Gemini 2.5 Flash if configured; falls back transparently
    to deterministic local analysis when offline or when AI is unavailable.
    """
    ensure_initialized()

    incident = get_incident(incident_id)
    if not incident:
        raise KeyError(f"Incident {incident_id!r} not found")

    # If cached analysis exists and force_refresh is False, return cached
    if not force_refresh:
        cached = get_root_cause_analysis(incident_id)
        if cached:
            try:
                return RootCauseAnalysisResponse(**cached)
            except Exception as exc:
                logger.warning("Failed to deserialize cached root-cause analysis: %s", exc)

    incident_service = incident.get("service") or "unknown"

    # Ingest / gather correlated events and dependencies
    correlated_resp = correlate_events_for_incident(incident_id, persist=True)
    raw_events = list_operational_events(incident_id)
    service_deps = get_service_dependencies(incident_service)
    catalog = build_evidence_catalog(incident_id, correlated_resp.events, raw_events)

    result: RootCauseAnalysisResponse | None = None
    fallback_reason: str | None = None

    # Try Gemini via Vertex AI if configured and not local fallback
    if GCP_PROJECT and not LOCAL_FALLBACK:
        try:
            result = await analyze_root_cause_with_gemini(
                incident=incident,
                correlated_events=correlated_resp.events,
                raw_events=raw_events,
                service_deps=service_deps,
                catalog=catalog,
            )
        except Exception as exc:
            logger.warning("Gemini root-cause analysis failed; falling back to deterministic engine: %s", exc)
            fallback_reason = f"Gemini API invocation failed: {type(exc).__name__}: {str(exc)[:200]}"

    # Fallback to deterministic local engine
    if result is None:
        result = analyze_root_cause_deterministic(
            incident=incident,
            correlated_events=correlated_resp.events,
            raw_events=raw_events,
            service_deps=service_deps,
            catalog=catalog,
        )
        if fallback_reason:
            result.fallback_reason = fallback_reason

    # Persist the analysis if requested
    if persist:
        payload = result.model_dump()
        save_root_cause_analysis(incident_id, payload)
        
        # Also synchronize diagnosis field in incident for backward compatibility
        top_hyp = result.hypotheses[0] if result.hypotheses else None
        diag = {
            "root_cause": (
                top_hyp.explanation
                if top_hyp
                else "Insufficient evidence to determine root cause."
            ),
            "category": incident.get("category") or "unknown",
            "severity": incident.get("severity") or "SEV3",
            "evidence": [
                {
                    "type": item.event_type,
                    "detail": f"{item.service} ({item.timestamp}): {item.description}",
                }
                for item in (top_hyp.supporting_evidence if top_hyp else [])
            ],
            "evidence_strength": top_hyp.confidence if top_hyp else "Possible",
            "unknowns": result.unverified_assumptions,
        }
        update_incident(incident_id, {"diagnosis": diag})

    return result
