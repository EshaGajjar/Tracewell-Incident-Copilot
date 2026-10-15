import { useEffect, useState } from "react";
import { api, request } from "../firebase.js";

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
  return EVENT_TYPES[key] || {
    icon: "⚙️",
    title: key.replaceAll("_", " ").replace(/\b\w/g, (c) => c.toUpperCase()),
  };
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

export default function IncidentDetail({ id }) {
  const [inc, setInc] = useState(null);
  const [steps, setSteps] = useState([]);
  const [selectedEvent, setSelectedEvent] = useState(null);
  const [investigating, setInvestigating] = useState(false);
  const [loadError, setLoadError] = useState("");

  useEffect(() => {
    let active = true;

    async function load() {
      try {
        const [incident, timeline] = await Promise.all([
          request(`/incidents/${encodeURIComponent(id)}`),
          request(`/incidents/${encodeURIComponent(id)}/steps`),
        ]);

        if (!active) return;

        setInc(incident);
        setSteps(Array.isArray(timeline) ? timeline : []);
        setLoadError("");
      } catch (error) {
        if (!active) return;

        setLoadError(error.message || "Unable to load incident.");
      }
    }

    load();

    const timer = setInterval(load, 5000);

    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [id]);

  async function startInvestigation() {
    setInvestigating(true);
    setLoadError("");

    try {
      await api("/investigate", { incident_id: id });

      const [incident, timeline] = await Promise.all([
        request(`/incidents/${encodeURIComponent(id)}`),
        request(`/incidents/${encodeURIComponent(id)}/steps`),
      ]);

      setInc(incident);
      setSteps(Array.isArray(timeline) ? timeline : []);
    } catch (error) {
      setLoadError(error.message || "Investigation failed.");
    } finally {
      setInvestigating(false);
    }
  }

  if (!inc && !loadError) {
    return <p className="muted">Loading incident...</p>;
  }

  if (!inc && loadError) {
    return <p className="error" role="alert">{loadError}</p>;
  }

  const diag = inc.diagnosis;
  const pending = inc.pending_action;
  const closed = ["resolved", "closed"].includes(inc.status);

  const sortedSteps = [...steps].sort((a, b) => {
    const timeA = a.ts?.seconds
      ? a.ts.seconds * 1000
      : new Date(a.ts || 0).getTime();

    const timeB = b.ts?.seconds
      ? b.ts.seconds * 1000
      : new Date(b.ts || 0).getTime();

    return timeA - timeB || (a.index ?? 0) - (b.index ?? 0);
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
          {steps.length} EVENTS
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

      {/* Incident Timeline */}

      <section className="card incident-timeline-section">
        <div className="timeline-header">
          <div>
            <h3>Incident Timeline</h3>
            <p>
              Explore investigation events and inspect their supporting
              evidence.
            </p>
          </div>

          <span className="timeline-count">
            {steps.length} EVENTS
          </span>
        </div>

        {sortedSteps.length === 0 ? (
          <div className="timeline-empty">
            <p>No investigation events yet.</p>
            <p>Start an investigation to collect evidence.</p>
          </div>
        ) : (
          <>
            <div className="timeline-grid">
              {sortedSteps.map((step, index) => {
                const event = getEventDetails(step);
                const selected =
                  selectedEvent === (step.id || `${step.index}-${index}`);

                return (
                  <button
                    type="button"
                    key={step.id || `${step.index}-${index}`}
                    className={`timeline-event-card ${
                      selected ? "selected" : ""
                    }`}
                    onClick={() =>
                      setSelectedEvent(
                        selected
                          ? null
                          : step.id || `${step.index}-${index}`
                      )
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
                          ? "Selected event"
                          : "Inspect evidence →"}
                      </span>
                    </span>
                  </button>
                );
              })}
            </div>

            {selectedEvent !== null && (() => {
              const selectedIndex = sortedSteps.findIndex(
                (step, index) =>
                  (step.id || `${step.index}-${index}`) === selectedEvent
              );

              const step = sortedSteps[selectedIndex];

              if (!step) return null;

              const event = getEventDetails(step);

              return (
                <div className="timeline-evidence">
                  <div className="timeline-evidence-label">
                    SELECTED EVENT
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

      {/* Diagnosis */}

      {diag && (
        <section className="card">
          <h3>
            Diagnosis{" "}
            <span className={`strength ${diag.evidence_strength}`}>
              {diag.evidence_strength || "Unrated"}
            </span>
          </h3>

          <p>{diag.root_cause}</p>

          {diag.evidence?.length > 0 && (
            <>
              <h4>Supporting evidence</h4>

              <ul>
                {diag.evidence.map((evidence, index) => (
                  <li key={index}>
                    <strong>{evidence.type || "Evidence"}:</strong>{" "}
                    {evidence.detail}
                  </li>
                ))}
              </ul>
            </>
          )}
        </section>
      )}

      {/* Recommended action */}

      {pending && (
        <ActionPanel id={id} pending={pending} />
      )}

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
    if (rootCause.trim().length < 3 || resolution.trim().length < 3) {
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
        <p className="muted">
          Decision: {actionStatus}
        </p>
      )}

      {error && <p className="error">{error}</p>}
    </section>
  );
}