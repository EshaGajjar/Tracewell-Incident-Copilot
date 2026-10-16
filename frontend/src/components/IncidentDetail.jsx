import { useEffect, useState } from "react";
import { api, request } from "../firebase.js";
import CausalGraphView from "./CausalGraphView.jsx";

const EVENT_TYPES = {
  investigation: { icon: "🕵️", title: "Investigation Started" },
  get_deployment_info: { icon: "🚀", title: "Deployment Information Retrieved" },
  get_logs: { icon: "📋", title: "Log Analysis" },
  search_knowledge: { icon: "📚", title: "Knowledge Base Search" },
  search_past_incidents: { icon: "📚", title: "Historical Incident Search" },
  check_service_health: { icon: "💚", title: "Service Health Check" },
  submit_diagnosis: { icon: "🔎", title: "Diagnosis Submitted" },
};

function getEventDetails(step) {
  const key = step.tool || step.type || "investigation";

  return (
    EVENT_TYPES[key] || {
      icon: "⚙️",
      title: key.replaceAll("_", " ").replace(/\b\w/g, (c) => c.toUpperCase()),
    }
  );
}

function formatTimestamp(value) {
  if (!value) return "Timestamp unavailable";

  let date;

  if (typeof value === "object" && value.seconds != null) {
    date = new Date(value.seconds * 1000);
  } else {
    date = new Date(value);
  }

  return Number.isNaN(date.getTime())
    ? "Timestamp unavailable"
    : date.toLocaleString();
}

function formatValue(value) {
  if (value == null) return "No evidence available";

  return typeof value === "string"
    ? value
    : JSON.stringify(value, null, 2);
}

function getStepKey(step, index) {
  return step.id || `${step.index ?? "step"}-${index}`;
}

export default function IncidentDetail({ id, onNavigateToDependencies }) {
  const [inc, setInc] = useState(null);
  const [steps, setSteps] = useState([]);
  const [operationalEvents, setOperationalEvents] = useState([]);
  const [selectedEvent, setSelectedEvent] = useState(null);
  const [investigating, setInvestigating] = useState(false);
  const [loadError, setLoadError] = useState("");
  const [operationalLoadError, setOperationalLoadError] = useState("");
  const [serviceDeps, setServiceDeps] = useState(null);
  const [serviceImpact, setServiceImpact] = useState(null);
  const [correlatedData, setCorrelatedData] = useState(null);
  const [correlationFilter, setCorrelationFilter] = useState("all");
  const [rootCauseAnalysis, setRootCauseAnalysis] = useState(null);
  const [analyzingRootCause, setAnalyzingRootCause] = useState(false);
  const [rcaError, setRcaError] = useState("");

  useEffect(() => {
    let active = true;

    async function load() {
      try {
        const [incident, timeline, events, corrRes, rcaRes] = await Promise.all([
          request(`/incidents/${encodeURIComponent(id)}`),
          request(`/incidents/${encodeURIComponent(id)}/steps`),
          request(`/incidents/${encodeURIComponent(id)}/events`),
          request(`/incidents/${encodeURIComponent(id)}/correlated-events`).catch(() => null),
          request(`/incidents/${encodeURIComponent(id)}/root-cause-analysis`).catch(() => null),
        ]);

        if (!active) return;

        setInc(incident);
        setSteps(Array.isArray(timeline) ? timeline : []);
        setOperationalEvents(Array.isArray(events) ? events : []);
        if (corrRes) setCorrelatedData(corrRes);
        if (rcaRes) setRootCauseAnalysis(rcaRes);
        setLoadError("");
        setOperationalLoadError("");

        // Fetch service dependencies and downstream impact if service is specified
        if (incident?.service) {
          try {
            const [depData, impactData] = await Promise.all([
              request(`/services/${encodeURIComponent(incident.service)}/dependencies`),
              request(`/services/${encodeURIComponent(incident.service)}/downstream-impact`),
            ]);
            if (active) {
              setServiceDeps(depData);
              setServiceImpact(impactData);
            }
          } catch {
            // Non-critical if dependencies not yet initialized
          }
        }
      } catch (error) {
        if (!active) return;

        setLoadError(error.message || "Unable to load incident.");
        setOperationalLoadError(
          error.message || "Unable to load operational events."
        );
      }
    }

    load();

    const timer = setInterval(load, 5000);

    return () => {
      active = false;

      clearInterval(timer);
    };
  }, [id]);

  async function triggerRootCauseAnalysis() {
    setAnalyzingRootCause(true);
    setRcaError("");

    try {
      const res = await api(
        `/incidents/${encodeURIComponent(id)}/root-cause-analysis`,
        {}
      );
      setRootCauseAnalysis(res);
      const updated = await request(`/incidents/${encodeURIComponent(id)}`);
      setInc(updated);
    } catch (err) {
      setRcaError(err.message || "Failed to generate root-cause analysis.");
    } finally {
      setAnalyzingRootCause(false);
    }
  }

  async function startInvestigation() {
    setInvestigating(true);
    setLoadError("");
    setOperationalLoadError("");

    try {
      await api("/investigate", { incident_id: id });

      const [incident, timeline, events, corrRes, rcaRes] = await Promise.all([
        request(`/incidents/${encodeURIComponent(id)}`),
        request(`/incidents/${encodeURIComponent(id)}/steps`),
        request(`/incidents/${encodeURIComponent(id)}/events`),
        request(`/incidents/${encodeURIComponent(id)}/correlated-events`).catch(() => null),
        request(`/incidents/${encodeURIComponent(id)}/root-cause-analysis`).catch(() => null),
      ]);

      setInc(incident);
      setSteps(Array.isArray(timeline) ? timeline : []);
      setOperationalEvents(Array.isArray(events) ? events : []);
      if (corrRes) setCorrelatedData(corrRes);
      if (rcaRes) setRootCauseAnalysis(rcaRes);
      setOperationalLoadError("");
    } catch (error) {
      setLoadError(error.message || "Investigation failed.");
      setOperationalLoadError(
        error.message || "Unable to load operational events."
      );
    } finally {
      setInvestigating(false);
    }
  }


  if (!inc && !loadError) {
    return <p className="muted">Loading incident...</p>;
  }

  if (!inc && loadError) {
    return (
      <p className="error" role="alert">
        {loadError}
      </p>
    );
  }

  const diag = inc.diagnosis;
  const pending = inc.pending_action;
  const closed = ["resolved", "closed"].includes(inc.status);

  const sortedSteps = [...steps].sort((a, b) => {
    const timeA =
      a.ts?.seconds != null
        ? a.ts.seconds * 1000
        : new Date(a.ts || 0).getTime();

    const timeB =
      b.ts?.seconds != null
        ? b.ts.seconds * 1000
        : new Date(b.ts || 0).getTime();

    return timeA - timeB || (a.index ?? 0) - (b.index ?? 0);
  });

  const sortedOperationalEvents = [...operationalEvents].sort((a, b) => {
    const timeA = new Date(a.timestamp || 0).getTime();
    const timeB = new Date(b.timestamp || 0).getTime();

    return timeA - timeB;
  });

  return (
    <div className="detail">
      <div className="incident-detail-heading">
        <div>
          <div className="eyebrow">INCIDENT INVESTIGATION</div>

          <h2>
            {inc.title}
            <span className={`status ${inc.status}`}>
              {inc.status?.replaceAll("_", " ")}
            </span>
          </h2>

          <p className="muted">
            {inc.service} · {inc.category || "Unclassified"} ·{" "}
            {inc.severity || "No severity"}
          </p>
        </div>

        <span className="timeline-count">
          {steps.length} INVESTIGATION STEPS
        </span>
      </div>

      {loadError && (
        <div className="notice error-notice" role="alert">
          {loadError}
        </div>
      )}

      <section className="card">
        <h3>Incident details</h3>

        <p>{inc.description}</p>

        {inc.attachments?.length > 0 && (
          <div className="shots">
            {inc.attachments.map((attachment, index) => (
              <a
                key={attachment.url || index}
                href={attachment.url}
                target="_blank"
                rel="noreferrer"
              >
                <img
                  src={attachment.url}
                  alt={attachment.name || "Incident attachment"}
                />
              </a>
            ))}
          </div>
        )}

        {!closed && inc.source === "human" && steps.length === 0 && (
          <button
            disabled={investigating}
            onClick={startInvestigation}
          >
            {investigating
              ? "Investigation running..."
              : "Investigate with Copilot"}
          </button>
        )}
      </section>

      {/* Service Dependencies & Cascading Impact */}
      {inc.service && (
        <section className="card service-impact-section">
          <div className="timeline-header">
            <div>
              <div className="eyebrow">DEPENDENCY TOPOLOGY & IMPACT</div>
              <h3>Service Dependencies & Cascading Impact</h3>
              <p>
                Architecture dependencies and downstream blast radius for <strong>{inc.service}</strong>.
              </p>
            </div>
            {onNavigateToDependencies && (
              <button
                className="secondary-button compact"
                onClick={() => onNavigateToDependencies(inc.service)}
              >
                Explore in Service Map →
              </button>
            )}
          </div>

          <div className="service-impact-grid">
            {/* Downstream Impact Column */}
            <div className="service-impact-col">
              <div className="impact-col-header">
                <strong>Potentially Affected Downstream Services</strong>
                <span className="impact-col-count">
                  {serviceImpact?.total_impacted ?? 0}
                </span>
              </div>
              {!serviceImpact || serviceImpact.impacted_services.length === 0 ? (
                <p className="muted">No downstream services depend on {inc.service}.</p>
              ) : (
                <div className="impact-badge-list">
                  {serviceImpact.impacted_services.map((item) => (
                    <div key={item.service_name} className={`impact-badge-item ${item.impact_level}`}>
                      <div className="impact-badge-header">
                        <span className="impact-service-title">{item.service_name}</span>
                        <span className={`impact-level-tag ${item.impact_level}`}>
                          {item.impact_level.toUpperCase()}
                        </span>
                      </div>
                      <div className="impact-path-small">
                        Depth {item.depth}: {item.dependency_path.join(" ──► ")}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* Upstream Dependencies Column */}
            <div className="service-impact-col">
              <div className="impact-col-header">
                <strong>Upstream Dependencies Required</strong>
                <span className="impact-col-count">
                  {serviceDeps?.upstream?.length ?? 0}
                </span>
              </div>
              {!serviceDeps || serviceDeps.upstream.length === 0 ? (
                <p className="muted">No upstream dependencies declared for {inc.service}.</p>
              ) : (
                <div className="upstream-badge-list">
                  {serviceDeps.upstream.map((dep) => (
                    <div key={dep.target} className="upstream-badge-item">
                      <div className="upstream-badge-header">
                        <span className="upstream-target-name">→ {dep.target}</span>
                        <span className={`status-pill ${dep.verification_status}`}>
                          {dep.verification_status}
                        </span>
                      </div>
                      <div className="upstream-meta">
                        <code>{dep.relationship_type.replace(/_/g, " ")}</code> · {dep.evidence}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        </section>
      )}

      {/* Incident Causal Graph Visualization */}
      <CausalGraphView incidentId={id} incidentService={inc?.service} />

      {/* Evidence-Based Event Correlation */}
      <section className="card correlation-section">
        <div className="timeline-header">
          <div>
            <div className="eyebrow">EVIDENCE-BASED CORRELATION</div>
            <h3>Correlated Operational Evidence</h3>
            <p>
              Events connected through exact service matching, revision metadata, and service dependencies.
              Temporal proximity alone does not establish causation.
            </p>
          </div>
          <div className="correlation-metrics-pills">
            <span className="corr-stat direct">
              {correlatedData?.direct_matches_count ?? 0} Direct
            </span>
            <span className="corr-stat dependency">
              {correlatedData?.dependency_related_count ?? 0} Dependency
            </span>
            <span className="corr-stat temporal">
              {correlatedData?.temporally_associated_count ?? 0} Temporal
            </span>
          </div>
        </div>

        {/* Filter Tabs */}
        {correlatedData && correlatedData.events.length > 0 && (
          <div className="correlation-filter-bar">
            <span className="filter-label">Filter:</span>
            <button
              type="button"
              className={`filter-chip ${correlationFilter === "all" ? "active" : ""}`}
              onClick={() => setCorrelationFilter("all")}
            >
              All ({correlatedData.total_events})
            </button>
            <button
              type="button"
              className={`filter-chip ${correlationFilter === "direct_match" ? "active" : ""}`}
              onClick={() => setCorrelationFilter("direct_match")}
            >
              Direct match ({correlatedData.direct_matches_count})
            </button>
            <button
              type="button"
              className={`filter-chip ${correlationFilter === "dependency_related" ? "active" : ""}`}
              onClick={() => setCorrelationFilter("dependency_related")}
            >
              Dependency-related ({correlatedData.dependency_related_count})
            </button>
            <button
              type="button"
              className={`filter-chip ${correlationFilter === "temporally_associated" ? "active" : ""}`}
              onClick={() => setCorrelationFilter("temporally_associated")}
            >
              Temporally associated ({correlatedData.temporally_associated_count})
            </button>
          </div>
        )}

        {!correlatedData || correlatedData.events.length === 0 ? (
          <div className="timeline-empty">
            <p>No operational events correlated for this incident yet.</p>
            <p className="muted">Events will be evaluated against service name, revisions, and dependencies as they are recorded.</p>
          </div>
        ) : (
          <div className="correlated-events-list">
            {correlatedData.events
              .filter(
                (ev) =>
                  correlationFilter === "all" ||
                  ev.classification === correlationFilter
              )
              .map((ev, idx) => {
                const isDirect = ev.classification === "direct_match";
                const isDep = ev.classification === "dependency_related";
                const isTemporal = ev.classification === "temporally_associated";

                return (
                  <article
                    key={ev.event_id || `corr-${idx}`}
                    className={`correlated-event-card ${ev.classification}`}
                  >
                    <div className="corr-card-topbar">
                      <div className="corr-service-info">
                        <span className="corr-icon">
                          {ev.event_type === "deployment"
                            ? "🚀"
                            : ev.event_type === "error_log"
                            ? "📋"
                            : ev.event_type === "health_check"
                            ? "💚"
                            : "⚡"}
                        </span>
                        <strong className="corr-service-name">{ev.service}</strong>
                        {ev.revision && (
                          <code className="corr-revision-tag">{ev.revision}</code>
                        )}
                        <span className={`corr-severity ${ev.severity?.toLowerCase()}`}>
                          {ev.severity || "INFO"}
                        </span>
                      </div>

                      <div className="corr-badge-group">
                        <span className={`corr-classification-badge ${ev.classification}`}>
                          {ev.classification_label}
                        </span>
                        <span className="corr-time">{formatTimestamp(ev.timestamp)}</span>
                      </div>
                    </div>

                    <p className="corr-description">{ev.description}</p>

                    {/* Correlation Reason Callout */}
                    <div className={`corr-reason-box ${ev.classification}`}>
                      <div className="corr-reason-title">
                        {isDirect && "🎯 Direct Service / Revision Match"}
                        {isDep && (
                          ev.relationship_direction === "upstream_dependency"
                            ? "⬆️ Upstream Dependency Factor (Potential Root Cause)"
                            : "⬇️ Downstream Dependent Impact (Cascading Symptom)"
                        )}
                        {isTemporal && "⏱️ Temporal Coincidence (Non-Causal Association)"}
                        {!isDirect && !isDep && !isTemporal && "ℹ️ Correlation Analysis"}
                      </div>
                      <p className="corr-reason-text">{ev.correlation_reason}</p>
                      {isTemporal && (
                        <div className="temporal-disclaimer">
                          ⚠️ Strict rule: Temporal proximity alone does not establish causation.
                        </div>
                      )}
                    </div>

                    {/* Evidence Inspector */}
                    {ev.evidence && Object.keys(ev.evidence).length > 0 && (
                      <details className="corr-evidence-details">
                        <summary>View evidence source ({ev.evidence_source})</summary>
                        <pre>{formatValue(ev.evidence)}</pre>
                      </details>
                    )}
                  </article>
                );
              })}
          </div>
        )}
      </section>

      {/* Operational Incident Timeline */}

      <section className="card incident-timeline-section">
        <div className="timeline-header">
          <div>
            <h3>Operational Incident Timeline</h3>
            <p>Actual service events recorded for this incident.</p>
          </div>

          <span className="timeline-count">
            {operationalEvents.length} EVENTS
          </span>
        </div>

        {operationalLoadError ? (
          <p className="error" role="alert">
            {operationalLoadError}
          </p>
        ) : sortedOperationalEvents.length === 0 ? (
          <div className="timeline-empty">
            <p>No operational events recorded yet.</p>
          </div>
        ) : (
          <div className="operational-event-list">
            {sortedOperationalEvents.map((event, index) => (
              <article
                className="timeline-evidence-block"
                key={event.id || `${event.event_type || "event"}-${index}`}
              >
                <h4>
                  {(event.event_type || "event")
                    .replaceAll("_", " ")
                    .replace(/\b\w/g, (c) => c.toUpperCase())}
                </h4>

                <p>{event.description || "No description available."}</p>

                <p className="muted">
                  {formatTimestamp(event.timestamp)} ·{" "}
                  {event.service || inc.service || "Unknown service"}
                </p>

                {event.evidence != null &&
                  typeof event.evidence === "object" &&
                  Object.keys(event.evidence).length > 0 && (
                    <details>
                      <summary>Inspect evidence</summary>
                      <pre>{formatValue(event.evidence)}</pre>
                    </details>
                  )}
              </article>
            ))}
          </div>
        )}
      </section>

      {/* AI Investigation Activity */}

      <section className="card incident-timeline-section">
        <div className="timeline-header">
          <div>
            <h3>AI Investigation Activity</h3>
            <p>
              Explore the steps the copilot took and inspect supporting
              evidence.
            </p>
          </div>

          <span className="timeline-count">
            {steps.length} INVESTIGATION STEPS
          </span>
        </div>

        {sortedSteps.length === 0 ? (
          <div className="timeline-empty">
            <p>No investigation activity yet.</p>
            <p>Start an investigation to collect evidence.</p>
          </div>
        ) : (
          <>
            <div className="timeline-grid">
              {sortedSteps.map((step, index) => {
                const event = getEventDetails(step);
                const stepKey = getStepKey(step, index);
                const selected = selectedEvent === stepKey;

                return (
                  <button
                    type="button"
                    key={stepKey}
                    className={`timeline-event-card ${
                      selected ? "selected" : ""
                    }`}
                    onClick={() =>
                      setSelectedEvent(selected ? null : stepKey)
                    }
                  >
                    <span className="timeline-event-icon">
                      {event.icon}
                    </span>

                    <span className="timeline-event-body">
                      <strong>{event.title}</strong>

                      <span className="timeline-event-time">
                        {formatTimestamp(step.ts)}
                      </span>

                      <span className="timeline-event-action">
                        {selected
                          ? "Selected step"
                          : "Inspect evidence →"}
                      </span>
                    </span>
                  </button>
                );
              })}
            </div>

            {selectedEvent !== null &&
              (() => {
                const selectedIndex = sortedSteps.findIndex(
                  (step, index) =>
                    getStepKey(step, index) === selectedEvent
                );

                const step = sortedSteps[selectedIndex];

                if (!step) return null;

                const event = getEventDetails(step);

                return (
                  <div className="timeline-evidence">
                    <div className="timeline-evidence-label">
                      SELECTED INVESTIGATION STEP
                    </div>

                    <h4>
                      {event.icon} {event.title}
                    </h4>

                    <p className="muted">
                      {formatTimestamp(step.ts)}
                    </p>

                    <div className="timeline-evidence-block">
                      <h4>Event type</h4>
                      <p>{step.type || "Investigation step"}</p>
                    </div>

                    {step.input != null && (
                      <div className="timeline-evidence-block">
                        <h4>Input</h4>
                        <pre>{formatValue(step.input)}</pre>
                      </div>
                    )}

                    {step.output != null && (
                      <div className="timeline-evidence-block">
                        <h4>Supporting evidence</h4>
                        <pre>{formatValue(step.output)}</pre>
                      </div>
                    )}
                  </div>
                );
              })()}
          </>
        )}
      </section>

      {/* Evidence-Based Root-Cause Analysis */}
      <section className="card root-cause-section">
        <div className="timeline-header">
          <div>
            <div className="eyebrow">EVIDENCE-BASED ROOT-CAUSE ANALYSIS</div>
            <h3>
              Root-Cause Hypotheses{" "}
              {rootCauseAnalysis?.hypotheses?.length > 0 && (
                <span className="rca-count-tag">
                  {rootCauseAnalysis.hypotheses.length} Ranked
                </span>
              )}
            </h3>
            <p>
              Hypotheses synthesized strictly from observable telemetry, deployment records, and verified service dependencies.
            </p>
          </div>

          <div className="rca-header-controls">
            {rootCauseAnalysis && (
              <span className={`rca-mode-badge ${rootCauseAnalysis.analysis_mode}`}>
                {rootCauseAnalysis.analysis_mode === "gemini_vertex_ai"
                  ? "✨ Gemini 2.5 Flash"
                  : "⚙️ Deterministic Fallback"}
              </span>
            )}
            <button
              type="button"
              className="action-btn-sm"
              onClick={triggerRootCauseAnalysis}
              disabled={analyzingRootCause}
            >
              {analyzingRootCause ? "Analyzing..." : "Re-analyze Root Cause"}
            </button>
          </div>
        </div>

        {rcaError && (
          <div className="notice error-notice" role="alert">
            {rcaError}
          </div>
        )}

        {/* Insufficient Evidence State */}
        {rootCauseAnalysis?.insufficient_evidence ? (
          <div className="insufficient-evidence-card">
            <div className="insufficient-header">
              <span className="insufficient-icon">⚠️</span>
              <div>
                <h4>Insufficient evidence to determine root cause.</h4>
                <p className="muted">
                  The operational telemetry evaluated does not contain sufficient direct deployment errors or upstream failures to establish a causal mechanism.
                </p>
              </div>
            </div>

            {rootCauseAnalysis.observed_facts?.length > 0 && (
              <div className="insufficient-facts">
                <strong>Observed Telemetry Findings:</strong>
                <ul>
                  {rootCauseAnalysis.observed_facts.map((fact, idx) => (
                    <li key={idx}>{fact}</li>
                  ))}
                </ul>
              </div>
            )}

            {rootCauseAnalysis.unverified_assumptions?.length > 0 && (
              <div className="insufficient-assumptions">
                <strong>Telemetry Gaps:</strong>
                <ul>
                  {rootCauseAnalysis.unverified_assumptions.map((assump, idx) => (
                    <li key={idx}>{assump}</li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        ) : rootCauseAnalysis?.hypotheses?.length > 0 ? (
          <div className="rca-hypotheses-list">
            {rootCauseAnalysis.hypotheses.map((hyp) => {
              return (
                <article
                  key={`hyp-${hyp.rank}`}
                  className={`rca-hypothesis-card rank-${hyp.rank}`}
                >
                  <div className="rca-card-topbar">
                    <div className="rca-badges-left">
                      <span className={`rca-rank-badge ${hyp.rank === 1 ? "top-rank" : ""}`}>
                        RANK #{hyp.rank}
                      </span>
                      <span className={`rca-confidence-badge ${hyp.confidence.toLowerCase()}`}>
                        {hyp.confidence}
                      </span>
                      <strong className="rca-service-tag">{hyp.suspected_service}</strong>
                      {hyp.suspected_component && (
                        <code className="rca-component-tag">{hyp.suspected_component}</code>
                      )}
                    </div>

                    <div className="rca-rationale-tag">
                      {hyp.ranking_rationale}
                    </div>
                  </div>

                  <h4 className="rca-explanation-heading">{hyp.explanation}</h4>

                  {/* Confidence Explanation */}
                  <div className="rca-confidence-box">
                    <strong>Confidence Justification:</strong> {hyp.confidence_explanation}
                  </div>

                  {/* Contradicting Evidence Banner */}
                  {hyp.contradicting_evidence?.length > 0 && (
                    <div className="rca-contradiction-box">
                      <div className="contradiction-title">⚠️ Contradicting or Weakening Signals:</div>
                      <ul>
                        {hyp.contradicting_evidence.map((contra, cidx) => (
                          <li key={cidx}>{contra}</li>
                        ))}
                      </ul>
                    </div>
                  )}

                  {/* 3-Way Breakdown: Facts vs Inferences vs Assumptions */}
                  <div className="rca-three-col-grid">
                    <div className="rca-col facts">
                      <div className="rca-col-title">Observed Facts</div>
                      <ul>
                        {hyp.observed_facts?.map((fact, fidx) => (
                          <li key={fidx}>{fact}</li>
                        ))}
                      </ul>
                    </div>

                    <div className="rca-col inferences">
                      <div className="rca-col-title">Inferred Relationships</div>
                      <ul>
                        {hyp.inferred_relationships?.map((inf, iidx) => (
                          <li key={iidx}>{inf}</li>
                        ))}
                      </ul>
                    </div>

                    <div className="rca-col assumptions">
                      <div className="rca-col-title">Unverified Assumptions</div>
                      <ul>
                        {hyp.unverified_assumptions?.map((ass, aidx) => (
                          <li key={aidx}>{ass}</li>
                        ))}
                      </ul>
                    </div>
                  </div>

                  {/* Supporting Evidence Items */}
                  {hyp.supporting_evidence?.length > 0 && (
                    <div className="rca-supporting-evidence-section">
                      <div className="rca-section-title">
                        Supporting Telemetry Evidence ({hyp.supporting_evidence.length})
                      </div>
                      <div className="rca-evidence-chips">
                        {hyp.supporting_evidence.map((ev, eidx) => (
                          <div key={ev.evidence_id || eidx} className="rca-evidence-chip">
                            <span className="ev-icon">
                              {ev.event_type === "deployment" ? "🚀" : ev.event_type === "error_log" ? "📋" : "⚡"}
                            </span>
                            <span className="ev-service">{ev.service}</span>
                            <span className="ev-time">{formatTimestamp(ev.timestamp)}</span>
                            <span className="ev-desc">{ev.description}</span>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* Recommended Verification Steps */}
                  {hyp.recommended_verification_steps?.length > 0 && (
                    <div className="rca-verification-steps">
                      <div className="rca-section-title">Recommended Pre-Remediation Verification Steps:</div>
                      <ol>
                        {hyp.recommended_verification_steps.map((step, sidx) => (
                          <li key={sidx}>{step}</li>
                        ))}
                      </ol>
                    </div>
                  )}

                  {/* Alternative Explanations */}
                  {hyp.alternative_explanations?.length > 0 && (
                    <div className="rca-alternatives">
                      <span className="alt-label">Alternative Plausible Explanations:</span>
                      <span> {hyp.alternative_explanations.join("; ")}</span>
                    </div>
                  )}
                </article>
              );
            })}
          </div>
        ) : diag ? (
          /* Fallback to simple diagnosis if full RCA is pending */
          <div className="timeline-empty">
            <p><strong>Diagnosis:</strong> {diag.root_cause}</p>
            <p className="muted">Click "Re-analyze Root Cause" to generate ranked hypotheses and evidence breakdown.</p>
          </div>
        ) : (
          <div className="timeline-empty">
            <p>No root-cause analysis generated yet.</p>
            <p className="muted">Click "Re-analyze Root Cause" above to evaluate operational events and service dependencies.</p>
          </div>
        )}
      </section>

      {/* Recommended action */}

      {pending && <ActionPanel id={id} pending={pending} />}

      {/* Verification */}

      {inc.verification && (
        <section className="card">
          <h3>Verification</h3>
          <pre>{formatValue(inc.verification)}</pre>
        </section>
      )}

      {/* Resolution */}

      {!closed && (
        <CloseForm
          id={id}
          onClosed={async () => {
            try {
              const updated = await request(
                `/incidents/${encodeURIComponent(id)}`
              );

              setInc(updated);
            } catch {
              // The regular refresh will reload the incident.
            }
          }}
        />
      )}

      {closed && inc.resolution && (
        <section className="card">
          <h3>Resolution</h3>

          <p>
            <strong>Root cause:</strong> {inc.root_cause}
          </p>

          <p>
            <strong>Resolution:</strong> {inc.resolution}
          </p>
        </section>
      )}

      {inc.report && (
        <section className="card">
          <h3>Incident report</h3>
          <pre className="report">{formatValue(inc.report)}</pre>
        </section>
      )}
    </div>
  );
}

function CloseForm({ id, onClosed }) {
  const [rootCause, setRootCause] = useState("");
  const [resolution, setResolution] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function closeIncident() {
    if (
      rootCause.trim().length < 3 ||
      resolution.trim().length < 3
    ) {
      setError("Please enter the root cause and resolution.");
      return;
    }

    setBusy(true);
    setError("");

    try {
      await api("/close", {
        incident_id: id,
        root_cause: rootCause.trim(),
        resolution: resolution.trim(),
      });

      await onClosed?.();
    } catch (err) {
      setError(err.message || "Unable to close incident.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card">
      <h3>Resolve incident</h3>

      <label>
        Root cause
        <textarea
          rows={3}
          value={rootCause}
          onChange={(event) => setRootCause(event.target.value)}
          placeholder="Explain what caused the incident..."
        />
      </label>

      <label>
        Resolution
        <textarea
          rows={3}
          value={resolution}
          onChange={(event) => setResolution(event.target.value)}
          placeholder="Describe how the incident was resolved..."
        />
      </label>

      <button disabled={busy} onClick={closeIncident}>
        {busy ? "Saving..." : "Close incident"}
      </button>

      {error && <p className="error">{error}</p>}
    </section>
  );
}

function ActionPanel({ id, pending }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [decisionMade, setDecisionMade] = useState(false);

  async function decide(path) {
    setBusy(true);
    setError("");

    try {
      await api(path, { incident_id: id });
      setDecisionMade(true);
    } catch (err) {
      setError(err.message || "Unable to record decision.");
    } finally {
      setBusy(false);
    }
  }

  const actionStatus = decisionMade
    ? "Decision submitted — refreshing incident state."
    : pending.status;

  return (
    <section className="card">
      <h3>Recommended action</h3>

      <p>
        <code>{pending.action}</code>
        {pending.reason ? ` — ${pending.reason}` : ""}
      </p>

      {pending.status === "pending" && !decisionMade ? (
        <div className="quick">
          <button
            disabled={busy}
            onClick={() => decide("/approve")}
          >
            {busy ? "Submitting..." : "Approve"}
          </button>

          <button
            disabled={busy}
            className="secondary"
            onClick={() => decide("/reject")}
          >
            Reject
          </button>
        </div>
      ) : (
        <p className="muted">Decision: {actionStatus}</p>
      )}

      {error && <p className="error">{error}</p>}
    </section>
  );
}