import React, { useEffect, useState } from "react";
import {
  AlertCircleIcon,
  CheckCircleIcon,
  ClockIcon,
  FileTextIcon,
  GitBranchIcon,
  LayersIcon,
  RefreshCwIcon,
  ServerIcon,
  SparklesIcon,
  TargetIcon,
} from "./Icons";

export const INVESTIGATION_STAGES = [
  {
    key: "retrieving_details",
    label: "Retrieving incident details",
    description: "Loading incident scope, affected service, and reporter inputs.",
    icon: FileTextIcon,
  },
  {
    key: "collecting_events",
    label: "Collecting operational events",
    description: "Querying deployments, error logs, and health check signals.",
    icon: ServerIcon,
  },
  {
    key: "correlating_events",
    label: "Correlating logs and deployment events",
    description: "Aligning temporal anomalies and matching against known patterns.",
    icon: GitBranchIcon,
  },
  {
    key: "analyzing_dependencies",
    label: "Analyzing service dependencies",
    description: "Evaluating topology, upstream callers, and downstream cascade risks.",
    icon: LayersIcon,
  },
  {
    key: "evaluating_hypotheses",
    label: "Evaluating root-cause hypotheses",
    description: "Scoring candidate fault hypotheses against observable evidence.",
    icon: TargetIcon,
  },
  {
    key: "preparing_diagnosis",
    label: "Preparing the evidence-backed diagnosis",
    description: "Synthesizing root cause findings and recommended remediation.",
    icon: SparklesIcon,
  },
];

/**
 * Polished, real-time investigation progress UI.
 *
 * Distinctly displays completed, current, and pending stages based on actual
 * backend execution. Does not invent fabricated percentages or simulate progress.
 */
export function InvestigationProgress({
  currentStageKey = "retrieving_details",
  stagesData = {},
  startTime = null,
  error = "",
  onRetry = null,
  isStreaming = true,
}) {
  const [elapsedMs, setElapsedMs] = useState(0);

  // Live timer tracking actual elapsed time
  useEffect(() => {
    if (!startTime) return;
    if (error) return; // Freeze timer on error

    const interval = setInterval(() => {
      setElapsedMs(Math.max(0, Date.now() - startTime));
    }, 100);

    return () => clearInterval(interval);
  }, [startTime, error]);

  const elapsedSec = (elapsedMs / 1000).toFixed(1);

  // Determine current active stage index
  const currentIndex = INVESTIGATION_STAGES.findIndex(
    (s) => s.key === currentStageKey
  );
  const activeIdx = currentIndex >= 0 ? currentIndex : 0;

  return (
    <section
      className="card investigation-progress-card"
      aria-live="polite"
      aria-busy={!error}
    >
      {/* Top indeterminate animated progress shimmer line */}
      <div className="investigation-shimmer-track">
        {!error && <div className="investigation-shimmer-bar" />}
      </div>

      {/* Main progress header */}
      <div className="investigation-progress-header">
        <div className="investigation-header-left">
          <div className="investigation-spinner-wrap">
            {!error ? (
              <div className="radar-spinner" aria-hidden="true">
                <span className="radar-ring radar-ring-outer" />
                <span className="radar-ring radar-ring-inner" />
                <RefreshCwIcon size={20} className="spin icon-center" />
              </div>
            ) : (
              <div className="error-icon-wrap" aria-hidden="true">
                <AlertCircleIcon size={24} style={{ color: "var(--status-error)" }} />
              </div>
            )}
          </div>

          <div>
            <div className="eyebrow" style={{ color: error ? "var(--status-error)" : "var(--brand-text)" }}>
              {error ? "INVESTIGATION INTERRUPTED" : "ACTIVE COPILOT INVESTIGATION"}
            </div>
            <h2 className="investigation-heading">
              {error ? "Investigation halted" : "Investigation in progress"}
            </h2>
            <p className="investigation-subtitle">
              {error
                ? "The investigation encountered an issue and could not complete automatically."
                : "Copilot is analyzing real-time signals, tracing service dependencies, and evaluating causal hypotheses."}
            </p>
          </div>
        </div>

        <div className="investigation-header-right">
          <div className="investigation-timer-badge" title="Investigation elapsed time">
            <ClockIcon size={13} />
            <span>Elapsed: {elapsedSec}s</span>
          </div>

          {!error && (
            <div className="investigation-mode-pill">
              <span className="pulse-dot" />
              <span>{isStreaming ? "Live SSE Stream" : "Indeterminate Sync"}</span>
            </div>
          )}
        </div>
      </div>

      {/* Error Notice & Retry Button */}
      {error && (
        <div className="investigation-error-banner" role="alert">
          <div className="investigation-error-content">
            <AlertCircleIcon size={18} style={{ color: "var(--status-error)", flexShrink: 0 }} />
            <div>
              <strong>Investigation failed to conclude</strong>
              <p>{error}</p>
            </div>
          </div>
          {onRetry && (
            <button
              type="button"
              className="primary-button"
              onClick={onRetry}
              style={{ flexShrink: 0 }}
            >
              <RefreshCwIcon size={14} />
              <span>Retry Investigation</span>
            </button>
          )}
        </div>
      )}

      {/* 6 Investigation Stages List */}
      <div className="investigation-stages-list" role="list">
        {INVESTIGATION_STAGES.map((stage, idx) => {
          const StageIcon = stage.icon;
          const stageInfo = stagesData[stage.key] || {};

          // Calculate stage state: completed, current, pending, or failed
          let state = "pending";
          if (error && idx === activeIdx) {
            state = "failed";
          } else if (stageInfo.status === "completed" || idx < activeIdx) {
            state = "completed";
          } else if (idx === activeIdx) {
            state = "current";
          }

          // Truthful detail message: backend output message if present, or fallback to description
          const detailMessage =
            stageInfo.message ||
            (state === "completed"
              ? "Completed successfully."
              : stage.description);

          return (
            <div
              key={stage.key}
              className={`investigation-stage-row ${state}`}
              role="listitem"
            >
              <div className="stage-left-rail">
                {/* Visual state indicator */}
                <div className={`stage-status-bullet ${state}`}>
                  {state === "completed" && (
                    <CheckCircleIcon size={16} style={{ color: "var(--status-success)" }} />
                  )}
                  {state === "current" && (
                    <RefreshCwIcon size={14} className="spin" style={{ color: "var(--brand-primary)" }} />
                  )}
                  {state === "failed" && (
                    <AlertCircleIcon size={15} style={{ color: "var(--status-error)" }} />
                  )}
                  {state === "pending" && (
                    <span className="bullet-hollow-circle" />
                  )}
                </div>

                {/* Vertical connecting rail between stages */}
                {idx < INVESTIGATION_STAGES.length - 1 && (
                  <div
                    className={`stage-connector-rail ${
                      idx < activeIdx ? "completed" : ""
                    }`}
                  />
                )}
              </div>

              <div className="stage-body">
                <div className="stage-title-row">
                  <div className="stage-title-group">
                    <StageIcon size={14} className="stage-category-icon" />
                    <span className="stage-title-text">{stage.label}</span>
                  </div>

                  <span className={`stage-state-badge ${state}`}>
                    {state === "completed" && "Completed"}
                    {state === "current" && (
                      <>
                        <span className="pulse-dot" /> In Progress
                      </>
                    )}
                    {state === "pending" && "Pending"}
                    {state === "failed" && "Failed"}
                  </span>
                </div>

                <p className="stage-detail-text">
                  {detailMessage}
                </p>

                {/* Sub-bar for active current stage */}
                {state === "current" && !error && (
                  <div className="stage-active-shimmer" />
                )}
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}
