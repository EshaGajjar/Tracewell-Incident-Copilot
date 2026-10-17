import { useEffect, useState, useMemo, useRef } from "react";
import { api, request, API_URL } from "../firebase.js";
import CausalGraphView from "./CausalGraphView.jsx";
import { InvestigationProgress } from "./InvestigationProgress.jsx";
import JsonCodeBlock from "./JsonCodeBlock.jsx";
import {
  SearchIcon,
  RocketIcon,
  FileTextIcon,
  BookOpenIcon,
  ClockIcon,
  HeartPulseIcon,
  TargetIcon,
  CheckCircleIcon,
  AlertCircleIcon,
  AlertTriangleIcon,
  TerminalIcon,
  ArrowUpRightIcon,
  ChevronRightIcon,
  RefreshCwIcon,
  ShieldIcon,
  DatabaseIcon,
  ActivityIcon,
  InfoIcon,
  XCircleIcon,
  ZapIcon,
} from "./Icons.jsx";

const EVENT_CONFIGS = {
  investigation: {
    icon: SearchIcon,
    title: "Investigation Started",
  },
  get_deployment_info: {
    icon: RocketIcon,
    title: "Deployment Information Retrieved",
  },
  get_logs: {
    icon: FileTextIcon,
    title: "Log Analysis",
  },
  search_knowledge: {
    icon: BookOpenIcon,
    title: "Knowledge Base Search",
  },
  search_past_incidents: {
    icon: ClockIcon,
    title: "Historical Incident Search",
  },
  check_service_health: {
    icon: HeartPulseIcon,
    title: "Service Health Check",
  },
  submit_diagnosis: {
    icon: TargetIcon,
    title: "Diagnosis Submitted",
  },
  investigation_completed: {
    icon: CheckCircleIcon,
    title: "Investigation Completed",
  },
  investigation_failed: {
    icon: AlertCircleIcon,
    title: "Investigation Failed",
  },
  agent_error: {
    icon: AlertTriangleIcon,
    title: "Investigation Error",
  },
};

function isLifecycleStep(step) {
  const tool = (step.tool || "").toLowerCase();
  const type = (step.type || "").toLowerCase();
  const lifecycleTools = [
    "investigation",
    "submit_diagnosis",
    "investigation_completed",
    "investigation_failed",
    "agent_error",
  ];
  return (
    lifecycleTools.includes(tool) ||
    ["message", "lifecycle", "diagnosis"].includes(type)
  );
}

function getStepSummary(step) {
  if (step.summary) return step.summary;
  const output = step.output;
  if (output == null) return "No result recorded";
  if (typeof output === "string") return output.slice(0, 160);
  if (typeof output === "object") {
    if (output.error) return `Error: ${output.error}`;
    if (output.status === "started") return "Tool execution started";
    if (step.tool === "get_deployment_info") {
      const revs = output.revisions || output.results;
      if (Array.isArray(revs)) return `Retrieved ${revs.length} deployment revision(s)`;
      return "Deployment metadata retrieved";
    }
    if (step.tool === "get_logs") {
      const logs = output.logs || output.results;
      if (Array.isArray(logs)) return `Analyzed ${logs.length} log entries`;
      return "Log analysis completed";
    }
    if (step.tool === "search_knowledge") {
      const docs = output.results;
      if (Array.isArray(docs)) return `Found ${docs.length} knowledge document(s)`;
      return "Knowledge base search completed";
    }
    if (step.tool === "search_past_incidents") {
      const incs = output.results;
      if (Array.isArray(incs)) return `Found ${incs.length} similar historical incident(s)`;
      return "Historical incident search completed";
    }
    if (step.tool === "check_service_health") {
      const status = output.status || output.health_status || "checked";
      return `Health check: ${status}`;
    }
    if (step.tool === "submit_diagnosis") {
      const cause = output.diagnosis?.root_cause || "Diagnosis submitted";
      return String(cause).slice(0, 160);
    }
    if (output.summary) return String(output.summary).slice(0, 160);
    if (output.message) return String(output.message).slice(0, 160);
  }
  return "";
}

function filterVisibleSteps(stepList) {
  if (!Array.isArray(stepList) || stepList.length === 0) return [];

  // Scoped completed execution IDs per run to prevent cross-run suppression
  const completedExecIds = new Set();
  for (const s of stepList) {
    if (s.type === "tool_result" && s.execution_id) {
      completedExecIds.add(`${s.run_id || ""}:${s.execution_id}`);
    }
  }

  const legacyResults = stepList.filter(
    (s) => s.type === "tool_result" && !s.execution_id
  );
  const usedLegacyIndices = new Set();
  const seenStepFingerprints = new Set();

  const visible = [];
  for (const s of stepList) {
    // Retry deduplication: exact same run, execution_id, step_type, and tool
    if (s.execution_id && s.type) {
      const fp = `${s.run_id || ""}:${s.execution_id}:${s.type}:${s.tool || ""}`;
      if (seenStepFingerprints.has(fp)) {
        continue;
      }
      seenStepFingerprints.add(fp);
    }

    if (isLifecycleStep(s)) {
      visible.push(s);
      continue;
    }

    const isStarted =
      s.type === "tool_call" &&
      (s.output?.status === "started" || (!s.output && !s.error));

    if (isStarted) {
      if (s.execution_id && completedExecIds.has(`${s.run_id || ""}:${s.execution_id}`)) {
        continue;
      }
      if (!s.execution_id) {
        let paired = false;
        for (let i = 0; i < legacyResults.length; i++) {
          if (usedLegacyIndices.has(i)) continue;
          if (legacyResults[i].tool === s.tool && (legacyResults[i].run_id || "") === (s.run_id || "")) {
            usedLegacyIndices.add(i);
            paired = true;
            break;
          }
        }
        if (paired) continue;
      }
    }

    visible.push(s);
  }

  return visible;
}

function groupStepsByRun(stepList) {
  if (!Array.isArray(stepList) || stepList.length === 0) return [];

  const runsMap = new Map();
  let currentInferredRun = "run_1";
  let legacyCounter = 0;

  for (const step of stepList) {
    let runId = step.run_id;
    if (!runId) {
      const tool = (step.tool || "").toLowerCase();
      const output = typeof step.output === "string" ? step.output.toLowerCase() : "";
      if (tool === "investigation" || output.includes("investigation started")) {
        legacyCounter++;
        currentInferredRun = `run_legacy_${legacyCounter}`;
      }
      runId = currentInferredRun;
    }
    if (!runsMap.has(runId)) {
      runsMap.set(runId, []);
    }
    runsMap.get(runId).push({ ...step, run_id: runId });
  }

  const runKeys = Array.from(runsMap.keys());
  const totalRuns = runKeys.length;
  const runs = [];

  for (let i = runKeys.length - 1; i >= 0; i--) {
    const runId = runKeys[i];
    const runSteps = runsMap.get(runId);
    const runNumber = i + 1;
    const isLatest = i === totalRuns - 1;
    const firstStep = runSteps[0] || {};
    const lastStep = runSteps[runSteps.length - 1] || {};

    let status = "in_progress";
    let diagnosis = null;
    for (const s of runSteps) {
      if (s.tool === "submit_diagnosis" && typeof s.output === "object" && s.output?.diagnosis) {
        diagnosis = s.output.diagnosis;
      }
      if (s.tool === "investigation_completed") {
        status = "completed";
      } else if (s.tool === "investigation_failed" || s.tool === "agent_error") {
        status = "failed";
      }
    }
    if (status === "in_progress" && (diagnosis || !isLatest)) {
      status = "completed";
    }

    runs.push({
      runId,
      runNumber,
      label: `Run #${runNumber}${isLatest ? " (Latest)" : ""}`,
      isLatest,
      status,
      startedAt: firstStep.ts,
      completedAt: status === "completed" || status === "failed" ? lastStep.ts : null,
      stepCount: runSteps.length,
      diagnosis,
      steps: runSteps,
    });
  }

  return runs;
}

function getEventDetails(step) {
  const key = step.tool || step.type || "investigation";
  const conf = EVENT_CONFIGS[key];
  if (conf) return conf;

  return {
    icon: TerminalIcon,
    title: key.replaceAll("_", " ").replace(/\b\w/g, (c) => c.toUpperCase()),
  };
}

function formatTimestamp(value) {
  if (!value) return "Unavailable";
  let date;
  if (typeof value === "object" && value.seconds != null) {
    date = new Date(value.seconds * 1000);
  } else {
    date = new Date(value);
  }
  return Number.isNaN(date.getTime()) ? "Unavailable" : date.toLocaleString();
}

function formatValue(value) {
  if (value == null) return "No data";
  return typeof value === "string" ? value : JSON.stringify(value, null, 2);
}

function getStepKey(step, index) {
  const runPrefix = step.run_id ? `${step.run_id}-` : "";
  if (step.execution_id) return `${runPrefix}exec-${step.execution_id}`;
  if (step.id != null) return `${runPrefix}id-${step.id}`;
  if (step.index != null) return `${runPrefix}idx-${step.index}`;
  return `${runPrefix}step-${index}`;
}

export default function IncidentDetail({
  id,
  autoInvestigate = false,
  onNavigateToDependencies,
  onIncidentUpdated,
  onBack,
}) {
  const [inc, setInc] = useState(null);
  const [steps, setSteps] = useState([]);
  const [operationalEvents, setOperationalEvents] = useState([]);
  const [correlatedData, setCorrelatedData] = useState(null);
  const [rootCauseAnalysis, setRootCauseAnalysis] = useState(null);
  const [serviceDeps, setServiceDeps] = useState(null);
  const [serviceImpact, setServiceImpact] = useState(null);

  const [activeTab, setActiveTab] = useState("root_cause"); // root_cause | timeline | causal_graph | topology | remediation
  const [selectedEvent, setSelectedEvent] = useState(null);
  const [selectedRunId, setSelectedRunId] = useState(null);
  const [correlationFilter, setCorrelationFilter] = useState("all");

  const [investigating, setInvestigating] = useState(false);
  const [investigationStages, setInvestigationStages] = useState({});
  const [currentStageKey, setCurrentStageKey] = useState("retrieving_details");
  const [investigationStartTime, setInvestigationStartTime] = useState(null);
  const [investigationError, setInvestigationError] = useState("");
  const [isStreaming, setIsStreaming] = useState(true);
  const abortControllerRef = useRef(null);
  const hasAutoTriggeredRef = useRef(false);

  const [analyzingRootCause, setAnalyzingRootCause] = useState(false);
  const [loadError, setLoadError] = useState("");
  const [rcaError, setRcaError] = useState("");

  // Poll incident and related telemetry
  useEffect(() => {
    if (!id) {
      setLoadError("No incident ID provided.");
      return;
    }

    let active = true;

    async function load() {
      try {
        const [
          incident,
          timeline,
          events,
          corrRes,
          rcaRes,
        ] = await Promise.all([
          request(`/incidents/${encodeURIComponent(id)}`),
          request(`/incidents/${encodeURIComponent(id)}/steps?raw=false`).catch(() => []),
          request(`/incidents/${encodeURIComponent(id)}/events`).catch(() => []),
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

        // Fetch topology
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
          } catch {}
        }
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

  // ALL HOOKS UNCONDITIONALLY DEFINED AT THE TOP LEVEL (BEFORE ANY CONDITIONAL RETURNS)
  const pending = inc?.pending_action;
  const closed = inc ? ["resolved", "closed"].includes(inc.status) : false;

  const tabBadge = useMemo(() => {
    if (!pending) {
      if (inc?.action_history && inc.action_history.length > 0) {
        return { label: "History", bg: "var(--bg-subtle)", color: "var(--text-muted)" };
      }
      return null;
    }
    const state = pending.lifecycle_state || pending.status;
    switch (state) {
      case "pending_approval":
      case "pending":
        return { label: "Pending Approval", bg: "var(--status-warning-subtle)", color: "var(--status-warning-text)" };
      case "approved_awaiting_execution":
      case "approved":
      case "approved_pending_execution":
        return { label: "Awaiting Execution", bg: "var(--brand-subtle)", color: "var(--brand-text)" };
      case "executing":
        return { label: "Executing…", bg: "var(--status-cyan-subtle)", color: "var(--status-cyan-text)" };
      case "executed_success":
      case "executed":
        return { label: "Executed", bg: "var(--status-success-subtle)", color: "var(--status-success-text)" };
      case "execution_failed":
      case "failed":
        return { label: "Failed", bg: "var(--status-error-subtle)", color: "var(--status-error-text)" };
      case "rejected":
        return { label: "Rejected", bg: "var(--bg-subtle)", color: "var(--text-muted)" };
      default:
        return { label: "Action", bg: "var(--brand-subtle)", color: "var(--brand-text)" };
    }
  }, [pending, inc?.action_history]);

  const sortedSteps = useMemo(() => {
    if (!Array.isArray(steps)) return [];
    return [...steps].sort((a, b) => {
      const timeA = a.ts?.seconds != null ? a.ts.seconds * 1000 : new Date(a.ts || 0).getTime();
      const timeB = b.ts?.seconds != null ? b.ts.seconds * 1000 : new Date(b.ts || 0).getTime();
      return timeA - timeB || (a.index ?? 0) - (b.index ?? 0);
    });
  }, [steps]);

  const visibleSteps = useMemo(() => {
    return filterVisibleSteps(sortedSteps);
  }, [sortedSteps]);

  const groupedRuns = useMemo(() => {
    return groupStepsByRun(visibleSteps);
  }, [visibleSteps]);

  const activeRun = useMemo(() => {
    if (!groupedRuns.length) return null;
    if (!selectedRunId || selectedRunId === "latest") {
      return groupedRuns[0];
    }
    return groupedRuns.find((r) => r.runId === selectedRunId) || groupedRuns[0];
  }, [groupedRuns, selectedRunId]);

  const displayedRuns = useMemo(() => {
    if (!groupedRuns.length) return [];
    if (selectedRunId === "all") return groupedRuns;
    return activeRun ? [activeRun] : [];
  }, [groupedRuns, activeRun, selectedRunId]);

  const leadingHypothesis = rootCauseAnalysis?.hypotheses?.[0];

  async function triggerRootCauseAnalysis() {
    setAnalyzingRootCause(true);
    setRcaError("");
    try {
      const res = await api(`/incidents/${encodeURIComponent(id)}/root-cause-analysis`, {});
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
    if (investigating) return; // Prevent duplicate investigation submissions
    const clientRunId = `run_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
    setInvestigating(true);
    setInvestigationError("");
    setLoadError("");
    setSelectedRunId(clientRunId);
    setInvestigationStartTime(Date.now());
    setCurrentStageKey("retrieving_details");
    setInvestigationStages({
      retrieving_details: {
        status: "in_progress",
        message: `Verifying incident scope and affected service for '${id}'...`,
      },
    });

    const controller = new AbortController();
    abortControllerRef.current = controller;
    const timeoutId = setTimeout(() => {
      controller.abort("timeout");
    }, 60000); // 60s timeout protection

    let streamCompleted = false;

    try {
      // 1. Try streaming updates via SSE endpoint with run_id
      let streamAttempted = false;
      try {
        const streamUrl = `${API_URL}/incidents/${encodeURIComponent(id)}/investigate/stream?run_id=${encodeURIComponent(clientRunId)}`;
        const resp = await fetch(streamUrl, {
          signal: controller.signal,
          headers: { Accept: "text/event-stream" },
        });

        if (resp.ok && resp.body) {
          streamAttempted = true;
          setIsStreaming(true);
          const reader = resp.body.getReader();
          const decoder = new TextDecoder();
          let buffer = "";

          while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });
            const chunks = buffer.split("\n\n");
            buffer = chunks.pop() || "";

            for (const chunk of chunks) {
              const dataLine = chunk.trim().replace(/^data:\s*/, "");
              if (!dataLine) continue;
              try {
                const event = JSON.parse(dataLine);
                if (event.stage === "error" || event.status === "failed") {
                  throw new Error(event.message || "Investigation failed.");
                }

                if (event.stage === "complete") {
                  streamCompleted = true;
                } else if (event.stage) {
                  setCurrentStageKey(event.stage);
                  setInvestigationStages((prev) => ({
                    ...prev,
                    [event.stage]: {
                      status: event.status,
                      message: event.message,
                      label: event.label,
                      data: event.data,
                    },
                  }));
                }
              } catch (parseErr) {
                if (parseErr.message && !parseErr.message.includes("JSON")) {
                  throw parseErr;
                }
              }
            }
          }
        }
      } catch (streamErr) {
        if (controller.signal.aborted) {
          throw streamErr;
        }
        console.warn("SSE stream failed or unavailable, falling back to POST /investigate", streamErr);
      }

      // 2. If SSE stream didn't finish or wasn't available, fall back to POST /investigate with truthful indeterminate progress
      if (!streamCompleted) {
        setIsStreaming(false);
        setCurrentStageKey("collecting_events");
        setInvestigationStages((prev) => ({
          ...prev,
          retrieving_details: { status: "completed", message: "Incident record verified." },
          collecting_events: { status: "in_progress", message: "Gathering operational evidence and evaluating causal hypotheses..." },
        }));

        await api(
          "/investigate",
          { incident_id: id, run_id: clientRunId },
          { signal: controller.signal, timeoutMs: 58000 }
        );
        streamCompleted = true;
      }

      clearTimeout(timeoutId);

      // Refresh incident and all associated findings
      const [incident, timeline, events, corrRes, rcaRes] = await Promise.all([
        request(`/incidents/${encodeURIComponent(id)}`),
        request(`/incidents/${encodeURIComponent(id)}/steps?raw=false`).catch(() => []),
        request(`/incidents/${encodeURIComponent(id)}/events`).catch(() => []),
        request(`/incidents/${encodeURIComponent(id)}/correlated-events`).catch(() => null),
        request(`/incidents/${encodeURIComponent(id)}/root-cause-analysis`).catch(() => null),
      ]);
      setInc(incident);
      setSteps(Array.isArray(timeline) ? timeline : []);
      setOperationalEvents(Array.isArray(events) ? events : []);
      if (corrRes) setCorrelatedData(corrRes);
      if (rcaRes) setRootCauseAnalysis(rcaRes);
      onIncidentUpdated?.(incident);

      // Successfully finished: stop loader and render results
      setInvestigating(false);
      setInvestigationError("");
      setSelectedRunId(clientRunId);
    } catch (error) {
      clearTimeout(timeoutId);
      const isTimeout =
        controller.signal.aborted ||
        error.name === "AbortError" ||
        String(error).toLowerCase().includes("timeout");

      const msg = isTimeout
        ? "Investigation request timed out after 60 seconds. The backend may still be processing, or the connection stalled."
        : (error.message || "Investigation failed to execute.");

      setInvestigationError(msg);
      setInvestigating(false);
    } finally {
      clearTimeout(timeoutId);
      abortControllerRef.current = null;
    }
  }

  // Trigger automated investigation if requested on initial entry
  useEffect(() => {
    if (autoInvestigate && !hasAutoTriggeredRef.current && id) {
      hasAutoTriggeredRef.current = true;
      startInvestigation();
    }
  }, [autoInvestigate, id]);

  // Cleanup abort controller on unmount
  useEffect(() => {
    return () => {
      if (abortControllerRef.current) {
        abortControllerRef.current.abort();
      }
    };
  }, []);

  // 1. Missing or invalid ID error state
  if (!id) {
    return (
      <div className="card" style={{ borderLeft: "4px solid var(--status-error)", padding: "24px" }} role="alert">
        <div style={{ display: "flex", alignItems: "center", gap: "10px", marginBottom: "12px" }}>
          <AlertCircleIcon size={22} style={{ color: "var(--status-error)" }} />
          <h3 style={{ margin: 0, color: "var(--text-primary)" }}>Incident ID Missing</h3>
        </div>
        <p className="muted" style={{ marginBottom: "16px" }}>
          No incident ID was provided in the URL or navigation parameters.
        </p>
        {onBack && (
          <button className="primary-button" onClick={onBack}>
            ← Back to Overview
          </button>
        )}
      </div>
    );
  }

  // 2. Incident load error state
  if (!inc && loadError) {
    return (
      <div className="card" style={{ borderLeft: "4px solid var(--status-error)", padding: "24px" }} role="alert">
        <div style={{ display: "flex", alignItems: "center", gap: "10px", marginBottom: "12px" }}>
          <AlertCircleIcon size={22} style={{ color: "var(--status-error)" }} />
          <h3 style={{ margin: 0, color: "var(--text-primary)" }}>Unable to Load Incident</h3>
        </div>
        <p style={{ color: "var(--text-secondary)", marginBottom: "16px", lineHeight: "1.5" }}>
          {loadError} (Incident ID: <code>{id}</code>)
        </p>
        <div style={{ display: "flex", gap: "10px" }}>
          {onBack && (
            <button className="primary-button" onClick={onBack}>
              ← Back to Overview
            </button>
          )}
          <button className="secondary-button" onClick={() => window.location.reload()}>
            <RefreshCwIcon size={14} />
            <span>Retry</span>
          </button>
        </div>
      </div>
    );
  }

  // 3. Loading state
  if (!inc) {
    return (
      <div className="empty-state" style={{ padding: "48px 24px" }}>
        <RefreshCwIcon size={28} className="spin" style={{ color: "var(--brand-primary)" }} />
        <strong style={{ fontSize: "16px", marginTop: "12px" }}>Loading Incident Details</strong>
        <span className="muted" style={{ marginTop: "4px" }}>Retrieving telemetry, investigation timeline, and topology graphs…</span>
      </div>
    );
  }

  const diag = inc.diagnosis;

  return (
    <div className="detail">
      {/* ---------------- Header & Incident Metadata ---------------- */}
      <div className="incident-detail-heading">
        <div>
          <div className="eyebrow">
            INCIDENT INVESTIGATION <span className="eyebrow-line" />
          </div>
          <h2>
            <span>{inc.title}</span>
            <span className={`status ${inc.status}`}>
              {inc.status?.replaceAll("_", " ")}
            </span>
          </h2>
          <p className="muted">
            Service: <strong>{inc.service}</strong> · Category: {inc.category || "Unclassified"} · Severity: <strong>{inc.severity || "SEV3"}</strong>
          </p>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
          {!closed && (
            <button
              className="primary-button"
              disabled={investigating}
              onClick={startInvestigation}
              title={investigating ? "Investigation in progress" : "Run AI investigation"}
            >
              {investigating ? (
                <RefreshCwIcon size={14} className="spin" />
              ) : (
                <SearchIcon size={14} />
              )}
              <span>
                {investigating
                  ? "Investigating…"
                  : steps.length === 0
                  ? "Investigate with Copilot"
                  : "Re-run Investigation"}
              </span>
            </button>
          )}
          <span className="timeline-count">
            {visibleSteps.length} INVESTIGATION STEPS
          </span>
        </div>
      </div>

      {/* ---------------- Polished Real-Time Investigation Progress UI ---------------- */}
      {(investigating || investigationError) && (
        <InvestigationProgress
          currentStageKey={currentStageKey}
          stagesData={investigationStages}
          startTime={investigationStartTime}
          error={investigationError}
          onRetry={startInvestigation}
          isStreaming={isStreaming}
        />
      )}

      {loadError && (
        <div className="notice error-notice" role="alert">
          <AlertCircleIcon size={16} />
          <span>{loadError}</span>
        </div>
      )}

      {/* ---------------- Incident Description & Overview ---------------- */}
      <div className="card" style={{ padding: "16px 20px" }}>
        <h3 style={{ fontSize: "13px", color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: "6px" }}>
          Incident Summary
        </h3>
        <p style={{ fontSize: "14px", lineHeight: "1.6", color: "var(--text-primary)" }}>
          {inc.description}
        </p>
        {inc.attachments?.length > 0 && (
          <div className="shots" style={{ marginTop: "12px", display: "flex", gap: "10px" }}>
            {inc.attachments.map((att, idx) => (
              <a key={idx} href={att.url} target="_blank" rel="noreferrer">
                <img
                  src={att.url}
                  alt={att.name || "Attachment"}
                  style={{ maxHeight: "80px", borderRadius: "4px", border: "1px solid var(--border-subtle)" }}
                />
              </a>
            ))}
          </div>
        )}
      </div>

      {/* ---------------- Leading Root-Cause Hypothesis (Hero Card) ---------------- */}
      {leadingHypothesis && (
        <article className="hero-hypothesis-card">
          <div className="hero-hypothesis-top">
            <div className="hero-badges">
              <span className="rank-pill">RANK #1 TOP CAUSE</span>
              <span className={`confidence-pill ${(leadingHypothesis.confidence || "possible").toLowerCase()}`}>
                {leadingHypothesis.confidence || "Possible"}
              </span>
              <span className="suspected-service-chip">{leadingHypothesis.suspected_service}</span>
              {leadingHypothesis.suspected_component && (
                <span className="component-chip">
                  • {leadingHypothesis.suspected_component}
                </span>
              )}
            </div>
            <div className="muted" style={{ fontSize: "11px" }}>
              {leadingHypothesis.ranking_rationale}
            </div>
          </div>

          <div className="hero-explanation">{leadingHypothesis.explanation}</div>

          <div className="muted" style={{ fontSize: "12px", marginBottom: "12px" }}>
            <strong>Confidence Rationale:</strong> {leadingHypothesis.confidence_explanation}
          </div>

          {/* 3-Column Facts vs Inferences vs Assumptions Breakdown */}
          <div className="rca-three-col-grid">
            <div className="rca-col facts">
              <div className="rca-col-title">
                <CheckCircleIcon size={13} />
                <span>Observed Facts</span>
              </div>
              <ul>
                {leadingHypothesis.observed_facts?.map((fact, idx) => (
                  <li key={idx}>{fact}</li>
                ))}
              </ul>
            </div>

            <div className="rca-col inferences">
              <div className="rca-col-title">
                <ActivityIcon size={13} />
                <span>Inferred Relationships</span>
              </div>
              <ul>
                {leadingHypothesis.inferred_relationships?.map((inf, idx) => (
                  <li key={idx}>{inf}</li>
                ))}
              </ul>
            </div>

            <div className="rca-col assumptions">
              <div className="rca-col-title">
                <InfoIcon size={13} />
                <span>Unverified Assumptions</span>
              </div>
              <ul>
                {leadingHypothesis.unverified_assumptions?.map((ass, idx) => (
                  <li key={idx}>{ass}</li>
                ))}
              </ul>
            </div>
          </div>

          {/* Contradicting Signals */}
          {leadingHypothesis.contradicting_evidence?.length > 0 && (
            <div className="rca-contradiction-box">
              <div className="contradiction-title">
                ⚠️ Contradicting or Weakening Signals:
              </div>
              <ul>
                {leadingHypothesis.contradicting_evidence.map((c, idx) => (
                  <li key={idx}>{c}</li>
                ))}
              </ul>
            </div>
          )}

          {/* Verification steps */}
          {leadingHypothesis.recommended_verification_steps?.length > 0 && (
            <div className="rca-verification-steps">
              <div className="rca-section-title">
                Recommended Pre-Remediation Verification Steps:
              </div>
              <ol>
                {leadingHypothesis.recommended_verification_steps.map((st, idx) => (
                  <li key={idx}>{st}</li>
                ))}
              </ol>
            </div>
          )}
        </article>
      )}

      {/* ---------------- Navigation Tabs for Investigation Sections ---------------- */}
      <div className="detail-nav-tabs">
        <button
          className={`detail-tab ${activeTab === "root_cause" ? "active" : ""}`}
          onClick={() => setActiveTab("root_cause")}
        >
          <TargetIcon size={14} />
          <span>All Hypotheses & Evidence</span>
          {rootCauseAnalysis?.hypotheses?.length > 0 && (
            <span className="tab-badge">{rootCauseAnalysis.hypotheses.length}</span>
          )}
        </button>

        <button
          className={`detail-tab ${activeTab === "timeline" ? "active" : ""}`}
          onClick={() => setActiveTab("timeline")}
        >
          <ClockIcon size={14} />
          <span>Investigation Activity</span>
          <span className="tab-badge">{visibleSteps.length}</span>
        </button>

        <button
          className={`detail-tab ${activeTab === "causal_graph" ? "active" : ""}`}
          onClick={() => setActiveTab("causal_graph")}
        >
          <ActivityIcon size={14} />
          <span>Causal Graph</span>
        </button>

        <button
          className={`detail-tab ${activeTab === "topology" ? "active" : ""}`}
          onClick={() => setActiveTab("topology")}
        >
          <DatabaseIcon size={14} />
          <span>Cascading Impact & Topology</span>
        </button>

        <button
          className={`detail-tab ${activeTab === "remediation" ? "active" : ""}`}
          onClick={() => setActiveTab("remediation")}
        >
          <ShieldIcon size={14} />
          <span>Remediation & Actions</span>
          {tabBadge && (
            <span
              className="tab-badge"
              style={{ background: tabBadge.bg, color: tabBadge.color }}
            >
              {tabBadge.label}
            </span>
          )}
        </button>
      </div>

      {/* ---------------- TAB 1: ALL HYPOTHESES & CORRELATED EVIDENCE ---------------- */}
      {activeTab === "root_cause" && (
        <>
          {/* Re-analysis control */}
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
            <div className="muted">
              {rootCauseAnalysis?.analysis_mode === "gemini_vertex_ai"
                ? "✨ Vertex AI Structured Synthesis"
                : "⚙️ Deterministic Local Telemetry Engine"}
            </div>
            <button
              className="action-btn-sm"
              onClick={triggerRootCauseAnalysis}
              disabled={analyzingRootCause}
            >
              <RefreshCwIcon size={12} className={analyzingRootCause ? "spin" : ""} />
              <span>{analyzingRootCause ? "Analyzing…" : "Re-analyze Hypotheses"}</span>
            </button>
          </div>

          {rcaError && (
            <div className="notice error-notice" role="alert">
              <AlertCircleIcon size={15} />
              <span>{rcaError}</span>
            </div>
          )}

          {/* Insufficient Evidence State */}
          {rootCauseAnalysis?.insufficient_evidence && (
            <div className="card" style={{ borderLeft: "4px solid var(--status-warning)" }}>
              <div style={{ display: "flex", alignItems: "center", gap: "8px", marginBottom: "8px" }}>
                <AlertTriangleIcon size={18} color="var(--status-warning)" />
                <h4 style={{ color: "var(--status-warning-text)" }}>
                  Insufficient evidence to determine root cause.
                </h4>
              </div>
              <p className="muted" style={{ marginBottom: "12px" }}>
                The operational telemetry evaluated does not contain sufficient direct deployment errors or upstream failures to establish a causal mechanism.
              </p>
              {rootCauseAnalysis.observed_facts?.length > 0 && (
                <div style={{ marginBottom: "10px" }}>
                  <strong style={{ fontSize: "12px" }}>Observed Telemetry:</strong>
                  <ul style={{ margin: "4px 0", paddingLeft: "20px", fontSize: "12px", color: "var(--text-secondary)" }}>
                    {rootCauseAnalysis.observed_facts.map((f, i) => (
                      <li key={i}>{f}</li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          )}

          {/* Correlated Operational Evidence */}
          <section className="card correlation-section">
            <div className="timeline-header">
              <div>
                <div className="eyebrow">EVIDENCE-BASED CORRELATION</div>
                <h3>Correlated Operational Telemetry</h3>
                <p>
                  Events correlated through service boundaries, revision metadata, and topology.
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
            {correlatedData && (correlatedData.events || []).length > 0 && (
              <div className="correlation-filter-bar">
                <span className="filter-label">Filter:</span>
                <button
                  type="button"
                  className={`filter-chip ${correlationFilter === "all" ? "active" : ""}`}
                  onClick={() => setCorrelationFilter("all")}
                >
                  All ({correlatedData.total_events || (correlatedData.events || []).length})
                </button>
                <button
                  type="button"
                  className={`filter-chip ${correlationFilter === "direct_match" ? "active" : ""}`}
                  onClick={() => setCorrelationFilter("direct_match")}
                >
                  Direct match ({correlatedData.direct_matches_count || 0})
                </button>
                <button
                  type="button"
                  className={`filter-chip ${correlationFilter === "dependency_related" ? "active" : ""}`}
                  onClick={() => setCorrelationFilter("dependency_related")}
                >
                  Dependency-related ({correlatedData.dependency_related_count || 0})
                </button>
                <button
                  type="button"
                  className={`filter-chip ${correlationFilter === "temporally_associated" ? "active" : ""}`}
                  onClick={() => setCorrelationFilter("temporally_associated")}
                >
                  Temporally associated ({correlatedData.temporally_associated_count || 0})
                </button>
              </div>
            )}

            {!correlatedData || (correlatedData.events || []).length === 0 ? (
              <div className="empty-state">
                <p className="muted">No operational events correlated for this incident yet.</p>
              </div>
            ) : (
              <div className="correlated-events-list">
                {(correlatedData.events || [])
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

                        <div className="corr-reason-box">
                          <div className="corr-reason-title">
                            {isDirect && "Direct Service / Revision Match"}
                            {isDep &&
                              (ev.relationship_direction === "upstream_dependency"
                                ? "Upstream Dependency Factor (Potential Root Cause)"
                                : "Downstream Dependent Impact (Cascading Symptom)")}
                            {isTemporal && "Temporal Coincidence (Non-Causal Association)"}
                            {!isDirect && !isDep && !isTemporal && "Correlation Analysis"}
                          </div>
                          <p className="corr-reason-text">{ev.correlation_reason}</p>
                          {isTemporal && (
                            <div className="temporal-disclaimer">
                              ⚠️ Strict rule: Temporal proximity alone does not establish causation.
                            </div>
                          )}
                        </div>

                        {ev.evidence && Object.keys(ev.evidence).length > 0 && (
                          <details className="corr-evidence-details">
                            <summary>Inspect raw telemetry ({ev.evidence_source})</summary>
                            <pre>{formatValue(ev.evidence)}</pre>
                          </details>
                        )}
                      </article>
                    );
                  })}
              </div>
            )}
          </section>
        </>
      )}

      {/* ---------------- TAB 2: INVESTIGATION TIMELINE ---------------- */}
      {activeTab === "timeline" && (
        <section className="card incident-timeline-section">
          <div className="timeline-header">
            <div>
              <h3>AI Investigation Activity</h3>
              <p>Historical investigation runs with verified evidence and stable execution pairing.</p>
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
              {groupedRuns.length > 1 && (
                <span className="run-count-badge">
                  {groupedRuns.length} RUNS
                </span>
              )}
              <span className="timeline-count">
                {visibleSteps.length} INVESTIGATION STEPS
              </span>
            </div>
          </div>

          {/* Active in-progress banner */}
          {investigating && (
            <div className="active-run-banner" role="status">
              <RefreshCwIcon size={18} className="spin" style={{ color: "var(--brand-primary)" }} />
              <div className="active-run-banner-content">
                <strong>Investigation in Progress</strong>
                <p>Running live diagnostic pipeline and streaming verified evidence for the latest run...</p>
              </div>
            </div>
          )}

          {/* Run switcher toolbar when multiple runs exist */}
          {groupedRuns.length > 1 && (
            <div className="run-selector-toolbar">
              <span className="run-selector-label">Investigation Runs:</span>
              <div className="run-selector-pills">
                {groupedRuns.map((run) => {
                  const isActive =
                    (selectedRunId === null && run.isLatest) ||
                    (selectedRunId === "latest" && run.isLatest) ||
                    selectedRunId === run.runId;

                  return (
                    <button
                      key={run.runId}
                      type="button"
                      className={`run-pill-btn ${isActive ? "active" : ""} ${run.isLatest ? "latest" : ""}`}
                      onClick={() => setSelectedRunId(run.runId)}
                      title={`View steps for ${run.label}`}
                    >
                      <span className="run-pill-dot" />
                      <span>{run.label}</span>
                      <span className="run-pill-count">{run.steps.length}</span>
                    </button>
                  );
                })}
                <button
                  type="button"
                  className={`run-pill-btn ${selectedRunId === "all" ? "active" : ""}`}
                  onClick={() => setSelectedRunId("all")}
                  title="View all investigation runs in historical sequence"
                >
                  <span>All Runs</span>
                  <span className="run-pill-count">{visibleSteps.length}</span>
                </button>
              </div>
            </div>
          )}

          {visibleSteps.length === 0 ? (
            <div className="empty-state">
              <p className="muted">No investigation activity recorded yet.</p>
            </div>
          ) : (
            <>
              {displayedRuns.map((run) => (
                <div key={run.runId} className="investigation-run-group">
                  <div className="run-group-header">
                    <div className="run-group-identity">
                      <span className={`run-badge ${run.isLatest ? "latest" : "historical"}`}>
                        {run.isLatest ? "LATEST RUN" : `RUN #${run.runNumber}`}
                      </span>
                      <code className="run-id-pill">{run.runId}</code>
                      <span className={`run-status-tag ${run.status}`}>
                        {run.status === "completed"
                          ? "Completed"
                          : run.status === "in_progress"
                          ? "In Progress"
                          : "Failed"}
                      </span>
                    </div>
                    <div className="run-group-meta">
                      <span>Started: {formatTimestamp(run.startedAt)}</span>
                      {run.completedAt && (
                        <span>Finished: {formatTimestamp(run.completedAt)}</span>
                      )}
                      <span className="run-step-badge">{run.steps.length} step(s)</span>
                    </div>
                  </div>

                  {run.steps.length > 1 && (
                    <div className="run-sequence-bar" aria-label="Investigation sequence flow">
                      <span className="sequence-label">Step Flow:</span>
                      <div className="sequence-track">
                        {run.steps.map((st, i) => {
                          const isStFinal =
                            (i === run.steps.length - 1 &&
                              run.steps.length > 1 &&
                              run.status !== "in_progress") ||
                            st.tool === "investigation_completed" ||
                            st.tool === "investigation_failed";
                          const stLabel = isStFinal ? "Final Step" : `Step ${i + 1}`;
                          const stKey = getStepKey(st, i);
                          const isSelected = selectedEvent === stKey;
                          const stEvent = getEventDetails(st);
                          return (
                            <div key={stKey} className="sequence-node-wrap">
                              <button
                                type="button"
                                className={`sequence-node-btn ${isSelected ? "selected" : ""}`}
                                onClick={() => setSelectedEvent(isSelected ? null : stKey)}
                                title={`Inspect ${stLabel}: ${stEvent.title}`}
                              >
                                <span className="sequence-node-step">{stLabel}</span>
                                <span className="sequence-node-name">{stEvent.title}</span>
                              </button>
                              {i < run.steps.length - 1 && (
                                <span className="sequence-arrow" aria-hidden="true">→</span>
                              )}
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  )}

                  <div className="timeline-grid">
                    {run.steps.map((step, index) => {
                      const event = getEventDetails(step);
                      const IconComponent = event.icon;
                      const stepKey = getStepKey(step, index);
                      const selected = selectedEvent === stepKey;
                      const summary = getStepSummary(step);
                      const isFinal =
                        (index === run.steps.length - 1 &&
                          run.steps.length > 1 &&
                          run.status !== "in_progress") ||
                        step.tool === "investigation_completed" ||
                        step.tool === "investigation_failed";
                      const stepLabel = isFinal ? "Final Step" : `Step ${index + 1}`;

                      return (
                        <div
                          key={stepKey}
                          className={`timeline-step-row ${selected ? "is-selected" : ""}`}
                        >
                          <button
                            type="button"
                            className={`timeline-event-card ${selected ? "selected" : ""}`}
                            onClick={() => setSelectedEvent(selected ? null : stepKey)}
                            aria-expanded={selected}
                            aria-controls={`evidence-${stepKey}`}
                          >
                            <span className={`timeline-event-icon ${selected ? "selected" : ""}`}>
                              <IconComponent size={14} />
                            </span>

                            <div className="timeline-event-body">
                              <strong className="timeline-event-title">
                                <span className={`timeline-step-prefix ${isFinal ? "final" : ""}`}>
                                  {stepLabel}:
                                </span>{" "}
                                {event.title}
                              </strong>
                              {summary && (
                                <span className="timeline-event-summary">{summary}</span>
                              )}
                              <div className="timeline-event-meta">
                                <span className="timeline-event-time">
                                  {formatTimestamp(step.ts)}
                                </span>
                                {step.execution_id && (
                                  <code className="kbd-shortcut">
                                    id: {String(step.execution_id).slice(-8)}
                                  </code>
                                )}
                                <span className="timeline-event-action">
                                  {selected ? "Close inspection" : "Inspect evidence →"}
                                </span>
                              </div>
                            </div>
                          </button>

                          {/* Expanded evidence details immediately BELOW this step */}
                          {selected && (
                            <div
                              id={`evidence-${stepKey}`}
                              className="timeline-inline-evidence-container"
                              role="region"
                              aria-label={`${stepLabel} evidence details`}
                            >
                              <div className="timeline-evidence-branch">
                                <span className="branch-line" aria-hidden="true">└──</span>
                                <span className="branch-label">Expanded Evidence Details</span>
                              </div>

                              <div className="timeline-evidence-card">
                                <div className="timeline-evidence-card-header">
                                  <div className="evidence-header-main">
                                    <div className="evidence-header-title-row">
                                      <span className="evidence-step-icon">
                                        <IconComponent size={14} />
                                      </span>
                                      <h4 className="evidence-step-name">
                                        <span className={`evidence-step-badge ${isFinal ? "final" : ""}`}>
                                          {stepLabel}
                                        </span>
                                        <span>{event.title}</span>
                                      </h4>
                                    </div>

                                    <div className="evidence-header-badges">
                                      <span className="evidence-timestamp" title="Timestamp">
                                        <ClockIcon size={12} />
                                        <span>{formatTimestamp(step.ts)}</span>
                                      </span>

                                      {(step.run_id || run.runId) && (
                                        <span className="evidence-run-id" title="Investigation Run ID">
                                          <code className="run-id-pill">run: {step.run_id || run.runId}</code>
                                        </span>
                                      )}

                                      {step.execution_id && (
                                        <span className="evidence-exec-id" title="Execution ID">
                                          <code className="kbd-shortcut">
                                            exec: {String(step.execution_id).slice(-8)}
                                          </code>
                                        </span>
                                      )}

                                      {step.tool && (
                                        <span className="evidence-tool-badge" title="Invoked Tool">
                                          tool: {step.tool}
                                        </span>
                                      )}
                                    </div>
                                  </div>

                                  <button
                                    type="button"
                                    className="evidence-close-btn"
                                    onClick={(e) => {
                                      e.stopPropagation();
                                      setSelectedEvent(null);
                                    }}
                                    title="Collapse evidence panel"
                                    aria-label="Collapse evidence panel"
                                  >
                                    ✕ Close
                                  </button>
                                </div>

                                {(step.error || step.output?.error) && (
                                  <div className="timeline-evidence-error-banner" role="alert">
                                    <AlertTriangleIcon size={16} />
                                    <div className="error-banner-content">
                                      <strong>Investigation Step Warning / Error</strong>
                                      <p>{String(step.error || step.output?.error)}</p>
                                    </div>
                                  </div>
                                )}

                                {summary && (
                                  <div className="timeline-evidence-block">
                                    <div className="evidence-block-header">
                                      <h4>Result Summary</h4>
                                    </div>
                                    <p className="timeline-evidence-summary-text">{summary}</p>
                                  </div>
                                )}

                                {step.input != null && (
                                  <div className="timeline-evidence-block">
                                    <div className="evidence-block-header">
                                      <h4>Tool Arguments</h4>
                                    </div>
                                    <JsonCodeBlock data={step.input} label="tool arguments" />
                                  </div>
                                )}

                                {step.output != null && (
                                  <div className="timeline-evidence-block">
                                    <div className="evidence-block-header">
                                      <h4>Result Evidence Payload</h4>
                                    </div>
                                    <JsonCodeBlock data={step.output} label="result evidence payload" />
                                  </div>
                                )}

                                {step.input == null && step.output == null && !summary && (
                                  <div className="timeline-evidence-block">
                                    <p className="muted" style={{ fontSize: "12px", margin: 0 }}>
                                      No payload recorded for this investigation step.
                                    </p>
                                  </div>
                                )}
                              </div>
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                </div>
              ))}
            </>
          )}
        </section>
      )}

      {/* ---------------- TAB 3: CAUSAL GRAPH ---------------- */}
      {activeTab === "causal_graph" && (
        <CausalGraphView incidentId={id} incidentService={inc?.service} />
      )}

      {/* ---------------- TAB 4: SERVICE TOPOLOGY & IMPACT ---------------- */}
      {activeTab === "topology" && inc.service && (
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
                <span>Explore Full Map</span>
                <ArrowUpRightIcon size={12} />
              </button>
            )}
          </div>

          <div className="service-impact-grid">
            {/* Downstream Impact Column */}
            <div className="service-impact-col">
              <div className="impact-col-header">
                <strong>Potentially Affected Downstream Callers</strong>
                <span className="impact-col-count">
                  {serviceImpact?.total_impacted ?? 0}
                </span>
              </div>
              {!serviceImpact || (serviceImpact.impacted_services || []).length === 0 ? (
                <p className="muted">No downstream services depend on {inc.service}.</p>
              ) : (
                <div className="impact-badge-list">
                  {(serviceImpact.impacted_services || []).map((item) => (
                    <div key={item.service_name} className={`impact-badge-item ${item.impact_level || "low"}`}>
                      <div className="impact-badge-header">
                        <span className="impact-service-title">{item.service_name}</span>
                        <span className={`impact-level-tag ${item.impact_level || "low"}`}>
                          {(item.impact_level || "low").toUpperCase()}
                        </span>
                      </div>
                      <div className="impact-path-small">
                        Depth {item.depth}: {(item.dependency_path || []).join(" ──► ")}
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
              {!serviceDeps || (serviceDeps.upstream || []).length === 0 ? (
                <p className="muted">No upstream dependencies declared for {inc.service}.</p>
              ) : (
                <div className="upstream-badge-list">
                  {(serviceDeps.upstream || []).map((dep, idx) => (
                    <div key={dep.target || dep.target_service || `dep-${idx}`} className="upstream-badge-item">
                      <div className="upstream-badge-header">
                        <span className="upstream-target-name">→ {dep.target || dep.target_service}</span>
                        <span className={`status-pill ${dep.verification_status}`}>
                          {dep.verification_status}
                        </span>
                      </div>
                      <div className="upstream-meta">
                        <code>{(dep.relationship_type || "").replace(/_/g, " ")}</code> · {dep.evidence}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        </section>
      )}

      {/* ---------------- TAB 5: REMEDIATION & RESOLUTION ---------------- */}
      {activeTab === "remediation" && (
        <div style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
          <RemediationActionPanel
            id={id}
            pending={pending}
            actionHistory={inc.action_history || pending?.action_history || []}
            onActionCompleted={(updated) => {
              setInc(updated);
              onIncidentUpdated?.(updated);
            }}
          />

          {inc.verification && (
            <section className="card">
              <h3>Post-Remediation Verification Telemetry</h3>
              <pre>{formatValue(inc.verification)}</pre>
            </section>
          )}

          {!closed && (
            <CloseForm
              id={id}
              onClosed={async (updated) => {
                setInc(updated);
                onIncidentUpdated?.(updated);
              }}
            />
          )}

          {closed && inc.resolution && (
            <section className="card">
              <h3>Incident Resolved</h3>
              <p style={{ marginBottom: "6px" }}>
                <strong>Confirmed Root Cause:</strong> {inc.root_cause}
              </p>
              <p>
                <strong>Resolution Notes:</strong> {inc.resolution}
              </p>
            </section>
          )}

          {inc.report && (
            <section className="card">
              <h3>Postmortem & Remediation Report</h3>
              <pre style={{ maxHeight: "300px", overflow: "auto" }}>
                {formatValue(inc.report)}
              </pre>
            </section>
          )}
        </div>
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
    if (busy) return;
    if (rootCause.trim().length < 3 || resolution.trim().length < 3) {
      setError("Please enter root cause and resolution before closing.");
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
      const updated = await request(`/incidents/${encodeURIComponent(id)}`);
      await onClosed?.(updated);
    } catch (err) {
      setError(err.message || "Unable to close incident.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card">
      <h3>Resolve & Close Incident</h3>
      <div className="form-grid">
        <div className="form-group">
          <label>Confirmed Root Cause</label>
          <textarea
            rows={2}
            value={rootCause}
            onChange={(e) => setRootCause(e.target.value)}
            placeholder="Explain root cause based on verified telemetry…"
          />
        </div>
        <div className="form-group">
          <label>Resolution Summary</label>
          <textarea
            rows={2}
            value={resolution}
            onChange={(e) => setResolution(e.target.value)}
            placeholder="Document corrective actions taken…"
          />
        </div>
        <div>
          <button className="primary-button" disabled={busy} onClick={closeIncident}>
            <CheckCircleIcon size={14} />
            <span>{busy ? "Resolving…" : "Confirm Resolution"}</span>
          </button>
        </div>
        {error && <p className="notice error-notice">{error}</p>}
      </div>
    </section>
  );
}

function formatEventName(ev) {
  if (!ev) return "Action Event";
  switch (ev) {
    case "action_proposed":
    case "proposed":
      return "Action Proposed";
    case "action_approved":
    case "approved":
      return "Action Approved";
    case "action_rejected":
    case "rejected":
      return "Action Rejected";
    case "executed":
    case "execution_succeeded":
      return "Executed Successfully";
    case "execution_failed":
    case "failed":
      return "Execution Failed";
    default:
      return ev.replaceAll("_", " ").replace(/\b\w/g, (c) => c.toUpperCase());
  }
}

export function RemediationActionPanel({ id, pending, actionHistory = [], onActionCompleted }) {
  const [busy, setBusy] = useState(false);
  const [currentOp, setCurrentOp] = useState(""); // "approving" | "rejecting" | "executing"
  const [error, setError] = useState("");
  const [decisionNote, setDecisionNote] = useState("");
  const [simulateFailure, setSimulateFailure] = useState(false);
  const [optimisticState, setOptimisticState] = useState(null);

  // If there's an active optimistic state, merge it with pending
  const activePending = useMemo(() => {
    if (!pending) return null;
    if (!optimisticState) return pending;
    return { ...pending, ...optimisticState };
  }, [pending, optimisticState]);

  // Determine normalized lifecycle state
  const lifecycle = useMemo(() => {
    if (!activePending) return "none";
    if (activePending.lifecycle_state) return activePending.lifecycle_state;
    switch (activePending.status) {
      case "approved":
      case "approved_pending_execution":
        return "approved_awaiting_execution";
      case "executing":
        return "executing";
      case "executed":
        return "executed_success";
      case "failed":
        return "execution_failed";
      case "rejected":
        return "rejected";
      case "pending":
      case "pending_approval":
      default:
        return "pending_approval";
    }
  }, [activePending]);

  // Clear optimistic override once real pending changes from backend
  useEffect(() => {
    setOptimisticState(null);
  }, [pending?.lifecycle_state, pending?.status]);

  async function handleApprove() {
    if (busy) return;
    setBusy(true);
    setCurrentOp("approving");
    setError("");

    // Immediate UI update without waiting for page refresh
    setOptimisticState({
      lifecycle_state: "approved_awaiting_execution",
      status: "approved",
      decided_at: new Date().toISOString(),
      decision_note: decisionNote || undefined,
    });

    try {
      await api("/approve", {
        incident_id: id,
        notes: decisionNote || undefined,
      });
      const updated = await request(`/incidents/${encodeURIComponent(id)}`);
      setOptimisticState(null);
      setDecisionNote("");
      await onActionCompleted?.(updated);
    } catch (err) {
      setOptimisticState(null);
      setError(err.message || "Unable to record approval.");
    } finally {
      setBusy(false);
      setCurrentOp("");
    }
  }

  async function handleReject() {
    if (busy) return;
    setBusy(true);
    setCurrentOp("rejecting");
    setError("");

    // Immediate UI update without waiting for page refresh
    setOptimisticState({
      lifecycle_state: "rejected",
      status: "rejected",
      decided_at: new Date().toISOString(),
      decision_note: decisionNote || undefined,
    });

    try {
      await api("/reject", {
        incident_id: id,
        reason: decisionNote || undefined,
      });
      const updated = await request(`/incidents/${encodeURIComponent(id)}`);
      setOptimisticState(null);
      setDecisionNote("");
      await onActionCompleted?.(updated);
    } catch (err) {
      setOptimisticState(null);
      setError(err.message || "Unable to reject action.");
    } finally {
      setBusy(false);
      setCurrentOp("");
    }
  }

  async function handleExecute() {
    if (busy) return;
    setBusy(true);
    setCurrentOp("executing");
    setError("");

    // Immediate transition to executing state
    setOptimisticState({
      lifecycle_state: "executing",
      status: "executing",
    });

    try {
      await api("/execute", {
        incident_id: id,
        simulate_failure: simulateFailure,
        notes: decisionNote || undefined,
      });
      const updated = await request(`/incidents/${encodeURIComponent(id)}`);
      setOptimisticState(null);
      await onActionCompleted?.(updated);
    } catch (err) {
      setOptimisticState(null);
      setError(err.message || "Remediation execution request failed.");
    } finally {
      setBusy(false);
      setCurrentOp("");
    }
  }

  const historyList = useMemo(() => {
    const list = [...(actionHistory || []), ...(activePending?.action_history || [])];
    const seen = new Set();
    const result = [];
    for (const item of list) {
      const key = `${item.timestamp || ""}_${item.event || item.status || ""}`;
      if (!seen.has(key)) {
        seen.add(key);
        result.push(item);
      }
    }
    return result;
  }, [actionHistory, activePending?.action_history]);

  if (!activePending) {
    return (
      <section className="card" style={{ borderLeft: "4px solid var(--border-default)" }}>
        <div className="remediation-header-row">
          <div className="remediation-eyebrow">REMEDIATION & ACTIONS</div>
        </div>
        <p className="muted" style={{ margin: "12px 0", fontSize: "14px" }}>
          No active remediation proposals for this incident. Run root cause analysis or trigger an investigation to generate automated remediation recommendations.
        </p>
        {historyList.length > 0 && (
          <div className="remediation-audit-trail">
            <span style={{ fontWeight: 600, color: "var(--text-secondary)" }}>Audit Trail:</span>
            {historyList.map((entry, idx) => (
              <span key={idx} className="remediation-audit-step">
                {idx > 0 && <span>→</span>}
                <span>{formatTimestamp(entry.timestamp)}:</span>
                <strong>{formatEventName(entry.event || entry.status)}</strong>
                {entry.actor && <span className="muted">({entry.actor})</span>}
                {entry.note && <span>— "{entry.note}"</span>}
              </span>
            ))}
          </div>
        )}
      </section>
    );
  }

  const borderColors = {
    pending_approval: "var(--status-warning)",
    approved_awaiting_execution: "var(--brand-primary)",
    executing: "var(--status-cyan)",
    executed_success: "var(--status-success)",
    execution_failed: "var(--status-error)",
    rejected: "var(--border-default)",
  };

  return (
    <section
      className="remediation-lifecycle-card"
      style={{ borderLeft: `4px solid ${borderColors[lifecycle] || "var(--brand-primary)"}` }}
    >
      {/* 1. Header Row */}
      <div className="remediation-header-row">
        <div>
          <div className="remediation-eyebrow">
            {lifecycle === "pending_approval" && "Remediation Action • Awaiting Operator Review"}
            {lifecycle === "approved_awaiting_execution" && "Remediation Action • Authorized by Incident Commander"}
            {lifecycle === "executing" && "Remediation Action • Pipeline Execution In Flight"}
            {lifecycle === "executed_success" && "Remediation Action • Executed & Telemetry Verified"}
            {lifecycle === "execution_failed" && "Remediation Action • Execution Rollout Anomaly"}
            {lifecycle === "rejected" && "Remediation Action • Proposal Dismissed"}
          </div>
          <h3 className="remediation-title">
            {lifecycle === "pending_approval" && "Recommended Remediation Action"}
            {lifecycle === "approved_awaiting_execution" && "Approved — Awaiting Execution"}
            {lifecycle === "executing" && "Executing Remediation Rollback…"}
            {lifecycle === "executed_success" && "Remediation Executed Successfully"}
            {lifecycle === "execution_failed" && "Execution Failed"}
            {lifecycle === "rejected" && "Remediation Action Rejected"}
          </h3>
        </div>

        {/* State Badge */}
        {lifecycle === "pending_approval" && (
          <span className="remediation-lifecycle-badge pending">
            <ClockIcon size={12} />
            <span>Pending Approval</span>
          </span>
        )}
        {lifecycle === "approved_awaiting_execution" && (
          <span className="remediation-lifecycle-badge approved">
            <ShieldIcon size={12} />
            <span>Approved — Awaiting Execution</span>
          </span>
        )}
        {lifecycle === "executing" && (
          <span className="remediation-lifecycle-badge executing">
            <RefreshCwIcon size={12} className="spin" />
            <span>Executing…</span>
          </span>
        )}
        {lifecycle === "executed_success" && (
          <span className="remediation-lifecycle-badge executed">
            <CheckCircleIcon size={12} />
            <span>Executed Successfully</span>
          </span>
        )}
        {lifecycle === "execution_failed" && (
          <span className="remediation-lifecycle-badge failed">
            <AlertCircleIcon size={12} />
            <span>Execution Failed</span>
          </span>
        )}
        {lifecycle === "rejected" && (
          <span className="remediation-lifecycle-badge rejected">
            <XCircleIcon size={12} />
            <span>Rejected</span>
          </span>
        )}
      </div>

      {/* 2. Action Code & Metadata Box */}
      <div className="remediation-action-box">
        <div className="action-label">Target Infrastructure Action</div>
        <div className="action-code">
          <code>{activePending.action || "No action command specified"}</code>
        </div>
        <div style={{ display: "flex", flexWrap: "wrap", gap: "12px 20px", marginTop: "4px" }}>
          {activePending.service && (
            <div className="action-meta-item">
              <span className="meta-k">Service:</span>
              <span className="meta-v">{activePending.service}</span>
            </div>
          )}
          {activePending.target_revision && (
            <div className="action-meta-item">
              <span className="meta-k">Target Revision:</span>
              <span className="meta-v">{activePending.target_revision}</span>
            </div>
          )}
          {activePending.type && (
            <div className="action-meta-item">
              <span className="meta-k">Action Type:</span>
              <span className="meta-v">{activePending.type}</span>
            </div>
          )}
          {activePending.proposed_at && (
            <div className="action-meta-item">
              <span className="meta-k">Proposed:</span>
              <span className="meta-v">{formatTimestamp(activePending.proposed_at)}</span>
            </div>
          )}
        </div>
      </div>

      {/* 3. Reason / Investigation Rationale */}
      {activePending.reason && (
        <div className="remediation-reason-box">
          <span className="reason-label">Correlated Rationale & Diagnosis</span>
          <p>{activePending.reason}</p>
        </div>
      )}

      {/* 4. State-Specific Information & Callouts */}

      {/* State A: Pending Approval */}
      {lifecycle === "pending_approval" && (
        <>
          <div className="muted" style={{ fontSize: "12.5px", display: "flex", alignItems: "center", gap: "8px" }}>
            <AlertTriangleIcon size={14} style={{ color: "var(--status-warning)", flexShrink: 0 }} />
            <span>
              Production Safeguard: Risky infrastructure changes require explicit human authorization before execution. Approving records your authorization and enables execution.
            </span>
          </div>

          <div>
            <label style={{ display: "block", fontSize: "11px", fontWeight: 600, color: "var(--text-muted)", marginBottom: "4px" }}>
              Decision Note / Justification (optional)
            </label>
            <input
              type="text"
              className="remediation-note-input"
              placeholder="e.g., Authorized rollback to restore payment checkout SLA"
              value={decisionNote}
              onChange={(e) => setDecisionNote(e.target.value)}
              disabled={busy}
            />
          </div>

          <div className="remediation-actions-toolbar">
            <button
              className="primary-button"
              style={{ background: "var(--status-success)", borderColor: "var(--status-success)" }}
              disabled={busy}
              onClick={handleApprove}
            >
              <CheckCircleIcon size={14} className={busy && currentOp === "approving" ? "spin" : ""} />
              <span>{busy && currentOp === "approving" ? "Approving…" : "Approve Action"}</span>
            </button>
            <button
              className="secondary-button"
              disabled={busy}
              onClick={handleReject}
            >
              <XCircleIcon size={14} className={busy && currentOp === "rejecting" ? "spin" : ""} />
              <span>{busy && currentOp === "rejecting" ? "Rejecting…" : "Reject Action"}</span>
            </button>
          </div>
        </>
      )}

      {/* State B: Approved — Awaiting Execution */}
      {lifecycle === "approved_awaiting_execution" && (
        <>
          <div className="remediation-status-callout approved">
            <InfoIcon size={18} style={{ flexShrink: 0 }} />
            <div>
              <strong>Action Approved by Incident Commander</strong>
              <div>
                This remediation action is authorized. It has <strong>NOT</strong> yet been executed on live infrastructure. Initiate execution below to deploy the target revision.
              </div>
              {activePending.decision_note && (
                <div style={{ marginTop: "4px" }}>
                  <em>Approval note: "{activePending.decision_note}"</em>
                </div>
              )}
              <span className="failed-time">
                Approved at: {formatTimestamp(activePending.decided_at || activePending.approved_at)}
              </span>
            </div>
          </div>

          <div className="remediation-actions-toolbar">
            <button
              className="primary-button"
              disabled={busy}
              onClick={handleExecute}
            >
              <ZapIcon size={14} className={busy && currentOp === "executing" ? "spin" : ""} />
              <span>{busy && currentOp === "executing" ? "Executing…" : "Execute Remediation Action"}</span>
            </button>

            <label style={{ display: "inline-flex", alignItems: "center", gap: "6px", fontSize: "12px", cursor: "pointer", color: "var(--text-muted)", marginLeft: "8px" }}>
              <input
                type="checkbox"
                checked={simulateFailure}
                onChange={(e) => setSimulateFailure(e.target.checked)}
                disabled={busy}
              />
              <span>Simulate execution failure (test error handling)</span>
            </label>
          </div>
        </>
      )}

      {/* State C: Executing */}
      {lifecycle === "executing" && (
        <div className="remediation-executing-box">
          <RefreshCwIcon size={22} className="spin" style={{ color: "var(--brand-primary)", flexShrink: 0 }} />
          <div>
            <strong>Executing Remediation Rollback…</strong>
            <div className="muted" style={{ fontSize: "12.5px", marginTop: "2px" }}>
              Dispatching orchestration commands to cluster. Waiting for container rollout and health check consensus.
            </div>
          </div>
        </div>
      )}

      {/* State D: Executed Successfully */}
      {lifecycle === "executed_success" && (
        <>
          <div className="remediation-status-callout executed">
            <CheckCircleIcon size={18} style={{ flexShrink: 0 }} />
            <div>
              <strong>Execution Completed Successfully</strong>
              <div>{activePending.execution_result || "Action executed successfully. Live service recovered to target revision."}</div>
              <span className="failed-time">
                Executed at: {formatTimestamp(activePending.executed_at)}
              </span>
            </div>
          </div>

          <div className="remediation-evidence-card">
            <div className="evidence-header">
              <span className="evidence-title">DEPLOYMENT VERIFICATION EVIDENCE</span>
              <span className="evidence-time">
                Telemetry verified {formatTimestamp(activePending.executed_at)}
              </span>
            </div>
            <div className="evidence-grid">
              <div>
                <span className="ev-k">Target Service:</span>
                <span className="ev-v">{activePending.service || "payment-service"}</span>
              </div>
              <div>
                <span className="ev-k">Target Revision:</span>
                <span className="ev-v">{activePending.execution_evidence?.target_revision || activePending.target_revision || "v2.4.0"}</span>
              </div>
              <div>
                <span className="ev-k">Orchestrator Status:</span>
                <span className="ev-v success">{activePending.execution_evidence?.orchestrator_status || "healthy"}</span>
              </div>
              <div>
                <span className="ev-k">Post-Remediation Errors:</span>
                <span className="ev-v success">{activePending.execution_evidence?.error_rate_post_remediation || "0.00%"}</span>
              </div>
              <div>
                <span className="ev-k">Verification Check:</span>
                <span className="ev-v success">{activePending.execution_evidence?.verification_status || "verified_healthy"}</span>
              </div>
              <div>
                <span className="ev-k">Traffic Routing:</span>
                <span className="ev-v">100% to Target Revision</span>
              </div>
            </div>
          </div>
        </>
      )}

      {/* State E: Execution Failed */}
      {lifecycle === "execution_failed" && (
        <>
          <div className="remediation-status-callout failed">
            <AlertCircleIcon size={18} style={{ flexShrink: 0 }} />
            <div>
              <strong>Execution Failed</strong>
              <div>{activePending.error_message || "Execution encountered an error during deployment rollout."}</div>
              <span className="failed-time">
                Failed at: {formatTimestamp(activePending.failed_at)}
              </span>
            </div>
          </div>

          <div className="remediation-actions-toolbar">
            <button
              className="primary-button"
              style={{ background: "var(--status-warning)", borderColor: "var(--status-warning)" }}
              disabled={busy}
              onClick={handleExecute}
            >
              <RefreshCwIcon size={14} className={busy && currentOp === "executing" ? "spin" : ""} />
              <span>{busy && currentOp === "executing" ? "Retrying…" : "Retry Remediation Action"}</span>
            </button>

            <label style={{ display: "inline-flex", alignItems: "center", gap: "6px", fontSize: "12px", cursor: "pointer", color: "var(--text-muted)", marginLeft: "8px" }}>
              <input
                type="checkbox"
                checked={simulateFailure}
                onChange={(e) => setSimulateFailure(e.target.checked)}
                disabled={busy}
              />
              <span>Simulate execution failure</span>
            </label>
          </div>
        </>
      )}

      {/* State F: Rejected */}
      {lifecycle === "rejected" && (
        <div className="remediation-status-callout rejected">
          <XCircleIcon size={18} style={{ flexShrink: 0 }} />
          <div>
            <strong>Remediation Proposal Rejected</strong>
            <div>
              This action proposal was rejected and will not be applied to infrastructure. Execution is permanently blocked.
            </div>
            {activePending.decision_note && (
              <div style={{ marginTop: "4px" }}>
                <em>Rejection rationale: "{activePending.decision_note}"</em>
              </div>
            )}
            <span className="reject-time">
              Rejected at: {formatTimestamp(activePending.decided_at)}
            </span>
          </div>
        </div>
      )}

      {/* 5. Error display */}
      {error && <p className="notice error-notice">{error}</p>}

      {/* 6. Audit Trail */}
      {historyList.length > 0 && (
        <div className="remediation-audit-trail">
          <span style={{ fontWeight: 600, color: "var(--text-secondary)" }}>Audit Trail:</span>
          {historyList.map((entry, idx) => (
            <span key={idx} className="remediation-audit-step">
              {idx > 0 && <span>→</span>}
              <span>{formatTimestamp(entry.timestamp)}:</span>
              <strong>{formatEventName(entry.event || entry.status)}</strong>
              {entry.actor && <span className="muted">({entry.actor})</span>}
              {entry.note && <span>— "{entry.note}"</span>}
              {entry.error && <span>— (Error: {entry.error})</span>}
            </span>
          ))}
        </div>
      )}
    </section>
  );
}

export const ActionPanel = RemediationActionPanel;