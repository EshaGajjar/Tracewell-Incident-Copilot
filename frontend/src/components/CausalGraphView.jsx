import React, { useState, useMemo, useEffect, useCallback, useRef, memo } from "react";
import { request, api } from "../firebase.js";

const INITIAL_BATCH_SIZE = 25;
const BATCH_INCREMENT = 25;

const REFRESH_STAGES = [
  { id: "evidence", label: "Collecting operational evidence" },
  { id: "correlate", label: "Correlating deployments, errors, and health signals" },
  { id: "reevaluate", label: "Re-evaluating causal relationships" },
  { id: "update", label: "Updating the investigation graph" },
];

const NODE_ICONS = {
  service: "☁️",
  deployment: "🚀",
  error_event: "💥",
  health_event: "💚",
  root_cause_hypothesis: "🎯",
};

const NODE_TYPE_LABELS = {
  service: "Service",
  deployment: "Deployment Event",
  error_event: "Error Telemetry",
  health_event: "Health Check",
  root_cause_hypothesis: "Root Cause Hypothesis",
};

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
  if (value == null) return "No evidence details";
  return typeof value === "string" ? value : JSON.stringify(value, null, 2);
}

// -------------------------------------------------------------------
// Memoized Sub-Components for High-Performance Rendering
// -------------------------------------------------------------------

const GraphDefs = memo(function GraphDefs() {
  return (
    <defs>
      {/* Verified Arrow Marker */}
      <marker
        id="causal-arrow-verified"
        viewBox="0 0 10 10"
        refX="8"
        refY="5"
        markerWidth="6"
        markerHeight="6"
        orient="auto"
      >
        <path d="M 0 1 L 9 5 L 0 9 z" fill="var(--graph-edge-verified)" />
      </marker>

      {/* Configured Arrow Marker */}
      <marker
        id="causal-arrow-configured"
        viewBox="0 0 10 10"
        refX="8"
        refY="5"
        markerWidth="6"
        markerHeight="6"
        orient="auto"
      >
        <path d="M 0 1 L 9 5 L 0 9 z" fill="var(--graph-edge-configured)" />
      </marker>

      {/* Inferred Causal Arrow Marker */}
      <marker
        id="causal-arrow-inferred"
        viewBox="0 0 10 10"
        refX="8"
        refY="5"
        markerWidth="6"
        markerHeight="6"
        orient="auto"
      >
        <path d="M 0 1 L 9 5 L 0 9 z" fill="var(--graph-edge-inferred)" />
      </marker>

      {/* Neutral / Telemetry Arrow Marker */}
      <marker
        id="causal-arrow-neutral"
        viewBox="0 0 10 10"
        refX="8"
        refY="5"
        markerWidth="6"
        markerHeight="6"
        orient="auto"
      >
        <path d="M 0 1 L 9 5 L 0 9 z" fill="var(--graph-edge-neutral)" />
      </marker>

      {/* Selected / Focused Arrow Marker */}
      <marker
        id="causal-arrow-selected"
        viewBox="0 0 10 10"
        refX="8"
        refY="5"
        markerWidth="6"
        markerHeight="6"
        orient="auto"
      >
        <path d="M 0 1 L 9 5 L 0 9 z" fill="var(--brand-primary)" />
      </marker>

      {/* Highlighted Arrow Marker for refreshed edges */}
      <marker
        id="causal-arrow-highlight"
        viewBox="0 0 10 10"
        refX="8"
        refY="5"
        markerWidth="6"
        markerHeight="6"
        orient="auto"
      >
        <path d="M 0 1 L 9 5 L 0 9 z" fill="var(--status-cyan, #06b6d4)" />
      </marker>

      {/* Glow filter for leading hypothesis */}
      <filter id="glow-gold" x="-20%" y="-20%" width="140%" height="140%">
        <feDropShadow dx="0" dy="0" stdDeviation="4" floodColor="var(--status-warning)" floodOpacity="0.35" />
      </filter>
      <filter id="glow-cyan" x="-20%" y="-20%" width="140%" height="140%">
        <feDropShadow dx="0" dy="0" stdDeviation="4" floodColor="var(--brand-primary)" floodOpacity="0.4" />
      </filter>
      <filter id="glow-highlight" x="-25%" y="-25%" width="150%" height="150%">
        <feDropShadow dx="0" dy="0" stdDeviation="5" floodColor="var(--status-cyan, #06b6d4)" floodOpacity="0.75" />
      </filter>
    </defs>
  );
});

const GraphEdge = memo(function GraphEdge({
  edge,
  sourcePos,
  targetPos,
  sourceCol = 0,
  targetCol = 0,
  sourceDim = { w: 190, h: 56 },
  targetDim = { w: 190, h: 56 },
  parallelIndex = 0,
  parallelTotal = 1,
  isSelected = false,
  isNodeConnected = false,
  isDimmed = false,
  showAllLabels = false,
  isHovered = false,
  isRecentlyUpdated = false,
  onSelect,
  onHover,
}) {
  if (!sourcePos || !targetPos) return null;

  const isCausalRel =
    edge.relationship_type === "causes" ||
    edge.relationship_type === "triggers_error" ||
    edge.relationship_type === "propagates_to" ||
    edge.relationship_type === "hypothesized_cause";

  const strokeColor = isSelected
    ? "var(--brand-primary)"
    : isRecentlyUpdated
    ? "var(--status-cyan, #06b6d4)"
    : isNodeConnected
    ? "var(--brand-primary)"
    : edge.verification_status === "verified"
    ? "var(--graph-edge-verified)"
    : edge.verification_status === "inferred" || isCausalRel
    ? "var(--graph-edge-inferred)"
    : edge.verification_status === "configured"
    ? "var(--graph-edge-configured)"
    : "var(--graph-edge-neutral)";

  const markerId = isSelected || isNodeConnected
    ? "url(#causal-arrow-selected)"
    : isRecentlyUpdated
    ? "url(#causal-arrow-highlight)"
    : edge.verification_status === "verified"
    ? "url(#causal-arrow-verified)"
    : edge.verification_status === "inferred" || isCausalRel
    ? "url(#causal-arrow-inferred)"
    : edge.verification_status === "configured"
    ? "url(#causal-arrow-configured)"
    : "url(#causal-arrow-neutral)";

  const strokeDash =
    edge.verification_status === "configured"
      ? "6 4"
      : edge.verification_status === "inferred" || isCausalRel
      ? "5 3"
      : edge.relationship_type === "occurred_on"
      ? "2 4"
      : "none";

  // Collision-aware routing & parallel edge separation
  const colDiff = targetCol - sourceCol;
  const isSameColumn = colDiff === 0;
  const isLeftToRight = colDiff > 0;
  const bundleOffset = parallelTotal > 1 ? (parallelIndex - (parallelTotal - 1) / 2) * 14 : 0;

  let sx, sy, tx, ty, pathD, midX, midY;

  if (isSameColumn) {
    // Both nodes in same column: loop out to avoid cutting through intermediate nodes
    if (sourceCol === 0) {
      // Loop out to the left for column 0
      sx = sourcePos.x - sourceDim.w / 2;
      sy = sourcePos.y + bundleOffset;
      tx = targetPos.x - targetDim.w / 2 - 4;
      ty = targetPos.y + bundleOffset;
      const offset = -(46 + Math.min(60, Math.abs(ty - sy) * 0.14) + Math.abs(bundleOffset));
      pathD = `M ${sx} ${sy} C ${sx + offset} ${sy}, ${tx + offset} ${ty}, ${tx} ${ty}`;
      midX = sx + offset * 0.85;
      midY = (sy + ty) / 2;
    } else {
      // Loop out to the right for other columns
      sx = sourcePos.x + sourceDim.w / 2;
      sy = sourcePos.y + bundleOffset;
      tx = targetPos.x + targetDim.w / 2 + 4;
      ty = targetPos.y + bundleOffset;
      const offset = 46 + Math.min(60, Math.abs(ty - sy) * 0.14) + Math.abs(bundleOffset);
      pathD = `M ${sx} ${sy} C ${sx + offset} ${sy}, ${tx + offset} ${ty}, ${tx} ${ty}`;
      midX = sx + offset * 0.85;
      midY = (sy + ty) / 2;
    }
  } else if (isLeftToRight) {
    // Normal forward flow
    sx = sourcePos.x + sourceDim.w / 2;
    sy = sourcePos.y + bundleOffset;
    tx = targetPos.x - targetDim.w / 2 - 4; // leave 4px for arrowhead marker
    ty = targetPos.y + bundleOffset;
    const dx = tx - sx;

    if (colDiff === 1) {
      // Adjacent column: smooth cubic S-curve
      const cx1 = sx + dx * 0.45;
      const cy1 = sy;
      const cx2 = tx - dx * 0.45;
      const cy2 = ty;
      pathD = `M ${sx} ${sy} C ${cx1} ${cy1}, ${cx2} ${cy2}, ${tx} ${ty}`;
      midX = 0.125 * sx + 0.375 * cx1 + 0.375 * cx2 + 0.125 * tx;
      midY = 0.125 * sy + 0.375 * cy1 + 0.375 * cy2 + 0.125 * ty;
    } else {
      // Skipping 1 or 2 columns: arch vertically through channel to prevent cutting through cards
      const arcSign = bundleOffset !== 0 ? Math.sign(bundleOffset) : (sourcePos.y <= targetPos.y ? -1 : 1);
      const arcOffset = arcSign * (30 * (colDiff - 1) + Math.abs(bundleOffset));
      const cx1 = sx + dx * 0.32;
      const cy1 = sy + arcOffset;
      const cx2 = tx - dx * 0.32;
      const cy2 = ty + arcOffset;
      pathD = `M ${sx} ${sy} C ${cx1} ${cy1}, ${cx2} ${cy2}, ${tx} ${ty}`;
      midX = 0.125 * sx + 0.375 * cx1 + 0.375 * cx2 + 0.125 * tx;
      midY = 0.125 * sy + 0.375 * cy1 + 0.375 * cy2 + 0.125 * ty;
    }
  } else {
    // Backward flow: target is to the left of source
    sx = sourcePos.x - sourceDim.w / 2;
    sy = sourcePos.y + bundleOffset;
    tx = targetPos.x + targetDim.w / 2 + 4;
    ty = targetPos.y + bundleOffset;
    const dy = 48 + Math.abs(bundleOffset);
    const archY = Math.max(sy, ty) + dy;
    pathD = `M ${sx} ${sy} C ${sx - 35} ${archY}, ${tx + 35} ${archY}, ${tx} ${ty}`;
    midX = (sx + tx) / 2;
    midY = archY;
  }

  const relLabel = (edge.relationship_type || "relates_to").replace(/_/g, " ");
  const shouldShowLabel = isSelected || isHovered || isNodeConnected || showAllLabels;
  const edgeOpacity = isSelected || isNodeConnected || isRecentlyUpdated ? 1 : isDimmed ? 0.14 : 0.72;
  const edgeStrokeWidth = isSelected ? 3.2 : isRecentlyUpdated ? 3.0 : isNodeConnected ? 2.5 : 1.6;

  return (
    <g
      className={`causal-edge-group ${isSelected ? "selected" : ""} ${
        isNodeConnected ? "connected-focus" : ""
      } ${isDimmed ? "dimmed" : ""} ${isRecentlyUpdated ? "edge-highlight-changed" : ""}`}
      onClick={(e) => {
        e.stopPropagation();
        onSelect(edge.id);
      }}
      onMouseEnter={() => onHover && onHover(edge.id)}
      onMouseLeave={() => onHover && onHover(null)}
      style={{ cursor: "pointer", transition: "opacity 0.2s ease" }}
    >
      <title>{`${edge.relationship_type} (${edge.verification_status || "configured"} - ${edge.confidence || "normal"} confidence)`}</title>

      {/* Broad transparent hit-target for effortless clicking/hovering */}
      <path d={pathD} fill="none" stroke="transparent" strokeWidth="20" />

      {/* Visible Edge Path */}
      <path
        d={pathD}
        fill="none"
        stroke={strokeColor}
        strokeWidth={edgeStrokeWidth}
        strokeDasharray={strokeDash}
        markerEnd={markerId}
        opacity={edgeOpacity}
        style={{ transition: "stroke 0.2s ease, stroke-width 0.2s ease, opacity 0.2s ease" }}
      />

      {/* Relationship Label Pill — rendered on hover/selection or when toggled */}
      {shouldShowLabel && (
        <g
          className="edge-label-pill"
          transform={`translate(${midX}, ${midY})`}
          style={{ pointerEvents: "none" }}
        >
          <rect
            x={-Math.max(36, relLabel.length * 3.8)}
            y="-10"
            width={Math.max(72, relLabel.length * 7.6)}
            height="20"
            rx="5"
            fill="var(--bg-surface-elevated)"
            stroke={strokeColor}
            strokeWidth={isSelected ? 1.8 : 1.2}
            opacity="0.98"
            style={{ filter: "drop-shadow(0 2px 5px rgba(0,0,0,0.35))" }}
          />
          <text
            x="0"
            y="4"
            textAnchor="middle"
            fill={isSelected ? "var(--brand-primary)" : "var(--text-primary)"}
            fontSize="10"
            fontWeight="600"
            fontFamily="JetBrains Mono, monospace"
          >
            {relLabel}
          </text>
        </g>
      )}
    </g>
  );
});

const GraphNode = memo(function GraphNode({
  node,
  x,
  y,
  isSelected = false,
  isLeading = false,
  isIncidentService = false,
  isDirectlyConnected = false,
  isDimmed = false,
  isRecentlyUpdated = false,
  onSelect,
}) {
  const icon = NODE_ICONS[node.node_type] || "📦";
  const isHypothesis = node.node_type === "root_cause_hypothesis";
  const w = isHypothesis ? 220 : 190;
  const h = isHypothesis ? 64 : 56;
  const rx = 8;

  // Background and border styling using CSS variables (no hardcoded white!)
  let borderColor = "var(--graph-node-stroke)";
  let bgFill = "var(--graph-node-bg)";

  if (isSelected) {
    borderColor = "var(--graph-node-stroke-selected)";
    bgFill = "var(--graph-node-bg-selected)";
  } else if (isRecentlyUpdated) {
    borderColor = "var(--status-cyan, #06b6d4)";
    bgFill = "var(--status-cyan-subtle, rgba(6, 182, 212, 0.12))";
  } else if (isDirectlyConnected) {
    borderColor = "var(--brand-primary)";
    bgFill = "var(--bg-surface-elevated)";
  } else if (isLeading) {
    borderColor = "var(--status-warning)";
    bgFill = "var(--status-warning-subtle)";
  } else if (node.node_type === "deployment") {
    borderColor = "var(--brand-primary)";
    bgFill = "var(--brand-subtle)";
  } else if (node.node_type === "error_event") {
    borderColor = "var(--status-error)";
    bgFill = "var(--status-error-subtle)";
  } else if (node.node_type === "health_event") {
    borderColor = "var(--status-success)";
    bgFill = "var(--status-success-subtle)";
  } else if (node.node_type === "service" && isIncidentService) {
    borderColor = "var(--status-cyan)";
    bgFill = "var(--status-cyan-subtle)";
  }

  const filterEffect = isRecentlyUpdated
    ? "url(#glow-highlight)"
    : isLeading
    ? "url(#glow-gold)"
    : isSelected
    ? "url(#glow-cyan)"
    : "none";

  const nodeOpacity = isDimmed ? 0.28 : 1;

  return (
    <g
      className={`causal-node-group ${isSelected ? "selected" : ""} ${
        isLeading ? "leading-node" : ""
      } ${isDirectlyConnected ? "connected-node" : ""} ${isDimmed ? "dimmed" : ""} ${
        isRecentlyUpdated ? "node-highlight-changed" : ""
      }`}
      transform={`translate(${x - w / 2}, ${y - h / 2})`}
      onClick={(e) => {
        e.stopPropagation();
        onSelect(node.id);
      }}
      style={{
        cursor: "pointer",
        opacity: nodeOpacity,
        transition: "opacity 0.2s ease, transform 0.16s ease",
      }}
    >
      <title>{`${node.label} (${NODE_TYPE_LABELS[node.node_type] || node.node_type}) - ${node.service || "System"}`}</title>

      {/* Selected Accent Outer Ring */}
      {isSelected && (
        <rect
          x="-3"
          y="-3"
          width={w + 6}
          height={h + 6}
          rx={rx + 2}
          fill="none"
          stroke="var(--brand-primary)"
          strokeWidth="1.5"
          strokeOpacity="0.6"
        />
      )}

      {/* Node Box */}
      <rect
        width={w}
        height={h}
        rx={rx}
        fill={bgFill}
        stroke={borderColor}
        strokeWidth={isSelected ? 2.2 : isLeading ? 2 : 1.2}
        filter={filterEffect}
      />

      {/* Header row: Icon & Type Tag */}
      <text x="12" y="20" fontSize="13">
        {icon}
      </text>

      <text
        x="32"
        y="18"
        fill="var(--text-muted)"
        fontSize="8.5"
        fontWeight="600"
        fontFamily="JetBrains Mono, monospace"
      >
        {isLeading
          ? "LEADING CAUSE (RANK #1)"
          : NODE_TYPE_LABELS[node.node_type]?.toUpperCase() || "NODE"}
      </text>

      {/* Node Label / Title */}
      <text
        x="12"
        y="36"
        fill="var(--text-primary)"
        fontSize="11"
        fontWeight="600"
        fontFamily="Inter, sans-serif"
      >
        {node.label.length > 24 ? `${node.label.slice(0, 23)}…` : node.label}
      </text>

      {/* Subtitle / Service / Timestamp */}
      <text
        x="12"
        y="49"
        fill="var(--text-secondary)"
        fontSize="9"
        fontFamily="JetBrains Mono, monospace"
      >
        {node.service
          ? `svc: ${node.service}`
          : node.timestamp
          ? formatTimestamp(node.timestamp).split(",")[0]
          : ""}
      </text>
    </g>
  );
});

// -------------------------------------------------------------------
// Evidence Overview Component (Preserves Access to All 177+ Evidence Records)
// -------------------------------------------------------------------

const EvidenceOverviewDrawer = memo(function EvidenceOverviewDrawer({
  evidenceDetails,
  evidenceReferences,
  totalEntities,
  visibleCount,
  leadingHypothesis,
  onFocusEvidence,
}) {
  const [evidenceFilter, setEvidenceFilter] = useState("");

  const filteredEvidence = useMemo(() => {
    if (!evidenceDetails || evidenceDetails.length === 0) return [];
    if (!evidenceFilter.trim()) return evidenceDetails;
    const query = evidenceFilter.toLowerCase();
    return evidenceDetails.filter(
      (ev) =>
        (ev.evidence_id && ev.evidence_id.toLowerCase().includes(query)) ||
        (ev.service && ev.service.toLowerCase().includes(query)) ||
        (ev.summary && ev.summary.toLowerCase().includes(query)) ||
        (ev.event_type && ev.event_type.toLowerCase().includes(query))
    );
  }, [evidenceDetails, evidenceFilter]);

  return (
    <div className="inspector-content">
      <div className="inspector-header">
        <span className="inspector-type-pill">
          📋 Incident Evidence Archive
        </span>
      </div>

      <h4 className="inspector-title">All Telemetry & Evidence</h4>
      <p className="muted" style={{ fontSize: "12px", marginBottom: "14px" }}>
        Direct operational telemetry, deploy records, error traces, and service dependencies.
        Select any record below to focus and inspect it in the causal graph.
      </p>

      {/* Summary Stats Grid */}
      <div className="inspector-metadata-grid" style={{ marginBottom: "16px" }}>
        <div className="inspector-meta-row">
          <span className="meta-key">Total Entities:</span>
          <strong className="meta-val">{totalEntities}</strong>
        </div>
        <div className="inspector-meta-row">
          <span className="meta-key">Displayed in Graph:</span>
          <span className="meta-val text-blue">{visibleCount} nodes</span>
        </div>
        <div className="inspector-meta-row">
          <span className="meta-key">Evidence References:</span>
          <strong className="meta-val">{evidenceReferences?.length || 0} items</strong>
        </div>
        {leadingHypothesis && (
          <div className="inspector-meta-row">
            <span className="meta-key">Leading Cause:</span>
            <span className="meta-val" style={{ color: "var(--status-warning-text)" }}>
              #{leadingHypothesis.rank} {leadingHypothesis.suspected_service} ({leadingHypothesis.confidence})
            </span>
          </div>
        )}
      </div>

      {/* Search Filter for Evidence */}
      <div style={{ marginBottom: "12px" }}>
        <input
          type="text"
          className="search-input"
          placeholder="Filter evidence by ID, service, or text..."
          value={evidenceFilter}
          onChange={(e) => setEvidenceFilter(e.target.value)}
          style={{ width: "100%", fontSize: "12px", padding: "6px 10px" }}
        />
      </div>

      {/* Scrollable Evidence List */}
      <div className="inspector-evidence-list" style={{ maxHeight: "380px", overflowY: "auto" }}>
        {filteredEvidence.length === 0 ? (
          <p className="muted" style={{ fontSize: "12px", textAlign: "center", padding: "16px" }}>
            No evidence records match the search.
          </p>
        ) : (
          filteredEvidence.map((ev) => (
            <div key={ev.evidence_id} className="evidence-ref-card" style={{ marginBottom: "8px" }}>
              <div className="ev-card-top">
                <span className="ev-type-badge">
                  {ev.event_type === "deployment"
                    ? "🚀 Deployment"
                    : ev.event_type === "error_log"
                    ? "💥 Error Log"
                    : ev.event_type === "health_check"
                    ? "💚 Health Check"
                    : "📄 Telemetry"}
                </span>
                <span className="ev-time">{formatTimestamp(ev.timestamp)}</span>
              </div>
              <p className="ev-summary" style={{ fontSize: "12px", margin: "4px 0" }}>
                {ev.summary || "No description provided"}
              </p>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: "6px" }}>
                <span className="ev-svc" style={{ fontSize: "11px" }}>
                  svc: <strong>{ev.service || "unknown"}</strong> · ID: <code>{ev.evidence_id}</code>
                </span>
                <button
                  type="button"
                  className="action-btn-sm"
                  style={{ fontSize: "10px", padding: "2px 6px" }}
                  onClick={() => onFocusEvidence(ev.evidence_id)}
                  title="Focus entity in causal graph"
                >
                  Locate in Graph →
                </button>
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  );
});

// -------------------------------------------------------------------
// Main Causal Graph View Component
// -------------------------------------------------------------------

export default function CausalGraphView({ incidentId, incidentService }) {
  const [graphData, setGraphData] = useState(null);
  // Keep the latest graph available to async refreshes without making loadGraph
  // change identity on every graph update (which caused repeated reloads).
  const graphDataRef = useRef(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [refreshStageIndex, setRefreshStageIndex] = useState(0);
  const [recentlyUpdated, setRecentlyUpdated] = useState({
    nodeIds: new Set(),
    edgeIds: new Set(),
  });
  const [refreshSuccessNotice, setRefreshSuccessNotice] = useState("");
  const [error, setError] = useState("");
  const [selectedNodeId, setSelectedNodeId] = useState(null);
  const [selectedEdgeId, setSelectedEdgeId] = useState(null);
  const [hoveredEdgeId, setHoveredEdgeId] = useState(null);
  const [showAllLabels, setShowAllLabels] = useState(false);
  const [filterMode, setFilterMode] = useState("all");
  const [visibleNodeLimit, setVisibleNodeLimit] = useState(INITIAL_BATCH_SIZE);

  const refreshSeqRef = useRef(0);
  const stageTimersRef = useRef([]);
  const highlightTimerRef = useRef(null);
  const noticeTimerRef = useRef(null);

  const clearStageTimers = useCallback(() => {
    stageTimersRef.current.forEach((t) => clearTimeout(t));
    stageTimersRef.current = [];
  }, []);

  useEffect(() => {
    return () => {
      clearStageTimers();
      if (highlightTimerRef.current) clearTimeout(highlightTimerRef.current);
      if (noticeTimerRef.current) clearTimeout(noticeTimerRef.current);
    };
  }, [clearStageTimers]);

  // Fetch or retrieve persisted graph
  const loadGraph = useCallback(
    async (forceRefresh = false) => {
      if (forceRefresh && refreshing) {
        return;
      }

      const currentSeq = ++refreshSeqRef.current;
      clearStageTimers();

      if (forceRefresh) {
        setRefreshing(true);
        setRefreshStageIndex(0);
      } else {
        setLoading(true);
      }
      setError("");

      if (forceRefresh) {
        const t1 = setTimeout(() => {
          if (currentSeq === refreshSeqRef.current) {
            setRefreshStageIndex(1);
          }
        }, 350);

        const t2 = setTimeout(() => {
          if (currentSeq === refreshSeqRef.current) {
            setRefreshStageIndex(2);
          }
        }, 950);

        stageTimersRef.current = [t1, t2];
      }

      const startTime = Date.now();

      try {
        let data;
        if (forceRefresh) {
          data = await api(`/incidents/${encodeURIComponent(incidentId)}/causal-graph/refresh`, {});
        } else {
          data = await request(`/incidents/${encodeURIComponent(incidentId)}/causal-graph`);
        }

        if (currentSeq !== refreshSeqRef.current) {
          return;
        }

        clearStageTimers();

        if (forceRefresh) {
          setRefreshStageIndex(3);

          const incomingNodes = data?.nodes || [];
          const incomingEdges = data?.edges || [];
          const prevNodes = graphDataRef.current?.nodes || [];
          const prevEdges = graphDataRef.current?.edges || [];

          const prevNodeMap = new Map(prevNodes.map((n) => [n.id, n]));
          const prevEdgeMap = new Map(
            prevEdges.map((e) => [e.id || `${e.source}->${e.target}`, e])
          );

          const changedNodeIds = new Set();
          const changedEdgeIds = new Set();

          for (const node of incomingNodes) {
            const prev = prevNodeMap.get(node.id);
            if (!prev) {
              changedNodeIds.add(node.id);
            } else if (
              prev.confidence !== node.confidence ||
              prev.status !== node.status ||
              prev.severity !== node.severity ||
              prev.is_leading !== node.is_leading ||
              prev.hypothesis_score !== node.hypothesis_score ||
              prev.label !== node.label
            ) {
              changedNodeIds.add(node.id);
            }
          }

          for (const edge of incomingEdges) {
            const edgeKey = edge.id || `${edge.source}->${edge.target}`;
            const prev = prevEdgeMap.get(edgeKey);
            if (!prev) {
              changedEdgeIds.add(edge.id);
            } else if (
              prev.relationship_type !== edge.relationship_type ||
              prev.confidence !== edge.confidence ||
              prev.verification_status !== edge.verification_status
            ) {
              changedEdgeIds.add(edge.id);
            }
          }

          if (changedNodeIds.size === 0 && data?.leading_hypothesis_id) {
            changedNodeIds.add(data.leading_hypothesis_id);
          }

          setRecentlyUpdated({
            nodeIds: changedNodeIds,
            edgeIds: changedEdgeIds,
          });

          if (highlightTimerRef.current) clearTimeout(highlightTimerRef.current);
          highlightTimerRef.current = setTimeout(() => {
            setRecentlyUpdated({ nodeIds: new Set(), edgeIds: new Set() });
          }, 2500);

          setSelectedNodeId((prevId) => {
            if (prevId && incomingNodes.some((n) => n.id === prevId)) {
              return prevId;
            }
            return data?.leading_hypothesis_id || null;
          });

          setRefreshSuccessNotice(
            changedNodeIds.size > 0 || changedEdgeIds.size > 0
              ? "Graph updated with latest telemetry"
              : "Graph verified up to date"
          );
          if (noticeTimerRef.current) clearTimeout(noticeTimerRef.current);
          noticeTimerRef.current = setTimeout(() => {
            setRefreshSuccessNotice("");
          }, 3000);

          const elapsed = Date.now() - startTime;
          if (elapsed < 200) {
            await new Promise((r) => setTimeout(r, 200 - elapsed));
          }
        } else {
          // Set the leading hypothesis only when there is no valid current
          // selection. Preserve a user's selection across graph re-fetches.
          setSelectedNodeId((prevId) => {
            const nodeStillExists = prevId && (data?.nodes || []).some((n) => n.id === prevId);
            return nodeStillExists ? prevId : (data?.leading_hypothesis_id || null);
          });
          setVisibleNodeLimit(INITIAL_BATCH_SIZE);
        }

        graphDataRef.current = data;
        setGraphData(data);
      } catch (err) {
        if (currentSeq !== refreshSeqRef.current) return;
        clearStageTimers();
        setError(err.message || "Failed to load incident investigation graph.");
      } finally {
        if (currentSeq === refreshSeqRef.current) {
          setLoading(false);
          setRefreshing(false);
        }
      }
    },
    [incidentId, refreshing, clearStageTimers]
  );

  useEffect(() => {
    if (incidentId) {
      loadGraph(false);
    }
  }, [incidentId, loadGraph]);

  // Deduplicate and index all raw nodes by stable entity ID
  const deduplicatedNodes = useMemo(() => {
    if (!graphData?.nodes) return [];
    const seenNodeIds = new Set();
    const seenEntityKeys = new Set();
    const result = [];

    for (const node of graphData.nodes) {
      if (!node || !node.id) continue;
      if (seenNodeIds.has(node.id)) continue;

      const entityKey = `${node.node_type}:${node.entity_id || node.id}`;
      if (seenEntityKeys.has(entityKey)) continue;

      seenNodeIds.add(node.id);
      seenEntityKeys.add(entityKey);
      result.push(node);
    }
    return result;
  }, [graphData?.nodes]);

  // Fast node and evidence maps
  const nodeMap = useMemo(() => {
    const map = new Map();
    for (const n of deduplicatedNodes) {
      map.set(n.id, n);
    }
    return map;
  }, [deduplicatedNodes]);

  const evidenceMap = useMemo(() => {
    const map = new Map();
    if (graphData?.evidence_details) {
      for (const ev of graphData.evidence_details) {
        map.set(ev.evidence_id, ev);
      }
    }
    return map;
  }, [graphData?.evidence_details]);

  // Deduplicate edges and verify both endpoints exist
  const deduplicatedEdges = useMemo(() => {
    if (!graphData?.edges) return [];
    const validNodeIds = new Set(deduplicatedNodes.map((n) => n.id));
    const seenEdgeKeys = new Set();
    const result = [];

    for (const edge of graphData.edges) {
      if (!edge || !edge.id) continue;
      if (!validNodeIds.has(edge.source) || !validNodeIds.has(edge.target)) continue;

      const edgeKey = `${edge.source}->${edge.target}:${edge.relationship_type}`;
      if (seenEdgeKeys.has(edgeKey)) continue;

      seenEdgeKeys.add(edgeKey);
      result.push(edge);
    }
    return result;
  }, [graphData?.edges, deduplicatedNodes]);

  // Leading hypothesis reference
  const leadingHypothesisNode = useMemo(() => {
    if (!deduplicatedNodes) return null;
    return (
      deduplicatedNodes.find((n) => n.id === graphData?.leading_hypothesis_id) ||
      deduplicatedNodes.find((n) => n.is_leading_hypothesis) ||
      null
    );
  }, [deduplicatedNodes, graphData?.leading_hypothesis_id]);

  // Prioritize nodes for simplified causal path
  // Order: Leading hypothesis -> Incident service -> Suspected service ->
  // Deployments -> Most relevant error events -> Necessary service dependencies -> Remaining evidence
  const prioritizedNodes = useMemo(() => {
    if (!deduplicatedNodes || deduplicatedNodes.length === 0) return [];

    const leadingEvidenceIds = new Set(leadingHypothesisNode?.evidence_ids || []);
    const causalRels = new Set([
      "causes",
      "triggers_error",
      "propagates_to",
      "hypothesized_cause",
      "evidence_for",
    ]);

    // Track nodes with direct causal links
    const causalConnectedNodeIds = new Set();
    for (const edge of deduplicatedEdges) {
      if (causalRels.has(edge.relationship_type)) {
        causalConnectedNodeIds.add(edge.source);
        causalConnectedNodeIds.add(edge.target);
      }
    }

    function getNodeScore(node) {
      // 1. Leading Root Cause Hypothesis
      if (node.is_leading_hypothesis || node.id === leadingHypothesisNode?.id || node.metadata?.rank === 1) {
        return 10000;
      }
      // Other Hypotheses
      if (node.node_type === "root_cause_hypothesis") {
        return 9000 - (node.metadata?.rank || 9) * 100;
      }

      // 2. Incident Primary Service Node
      const isIncidentSvc =
        node.node_type === "service" &&
        (node.service === incidentService || node.metadata?.is_incident_service);
      if (isIncidentSvc) {
        return 8500;
      }

      // 3. Suspected Service of Leading Hypothesis
      const isSuspectedSvc =
        node.node_type === "service" &&
        (node.service === leadingHypothesisNode?.service ||
          node.service === leadingHypothesisNode?.metadata?.suspected_service);
      if (isSuspectedSvc) {
        return 8000;
      }

      const isLeadingEvidence =
        leadingEvidenceIds.has(node.entity_id) ||
        leadingEvidenceIds.has(node.id) ||
        (node.evidence_ids && node.evidence_ids.some((eid) => leadingEvidenceIds.has(eid)));
      const isCausallyLinked = causalConnectedNodeIds.has(node.id);

      // 4. Relevant Deployment Events (Triggers and Root-Cause Evidence)
      if (node.node_type === "deployment") {
        let score = 5000;
        if (isLeadingEvidence) score += 2500;
        if (isCausallyLinked) score += 1500;
        if (node.service === incidentService) score += 500;
        return score;
      }

      // 5. Most Relevant Error Events (Direct telemetry facts & corroborated errors)
      if (node.node_type === "error_event") {
        let score = 4000;
        if (isLeadingEvidence) score += 3000;
        if (isCausallyLinked) score += 2000;
        if (node.metadata?.classification === "root_cause_indicator") score += 1000;
        if (node.metadata?.classification === "correlated") score += 500;
        if (node.metadata?.severity === "CRITICAL") score += 300;
        else if (node.metadata?.severity === "ERROR") score += 200;
        if (node.service === incidentService) score += 100;
        return score;
      }

      // 6. Necessary Service Dependencies (Verified or direct upstream/downstream topology)
      if (node.node_type === "service") {
        let score = 3000;
        if (isCausallyLinked) score += 2000;
        if (node.metadata?.verification_status === "verified") score += 600;
        if (node.metadata?.is_upstream || node.metadata?.is_downstream) score += 400;
        return score;
      }

      // 7. Health Check Events
      if (node.node_type === "health_event") {
        let score = 2000;
        if (isLeadingEvidence) score += 1500;
        if (node.metadata?.severity === "CRITICAL" || node.metadata?.severity === "ERROR") score += 800;
        if (isCausallyLinked) score += 500;
        return score;
      }

      return 1000;
    }

    return [...deduplicatedNodes].sort((a, b) => {
      const scoreDiff = getNodeScore(b) - getNodeScore(a);
      if (scoreDiff !== 0) return scoreDiff;
      return a.id.localeCompare(b.id);
    });
  }, [deduplicatedNodes, leadingHypothesisNode, deduplicatedEdges, incidentService]);

  // Entity category counts for granular filter badges
  const counts = useMemo(() => {
    let hypotheses = 0;
    let deployments = 0;
    let errors = 0;
    let health = 0;
    let services = 0;
    for (const n of deduplicatedNodes) {
      if (n.node_type === "root_cause_hypothesis") hypotheses++;
      else if (n.node_type === "deployment") deployments++;
      else if (n.node_type === "error_event") errors++;
      else if (n.node_type === "health_event") health++;
      else if (n.node_type === "service") services++;
    }
    return { hypotheses, deployments, errors, health, services };
  }, [deduplicatedNodes]);

  // Filter prioritized nodes by filterMode
  const filteredPrioritizedNodes = useMemo(() => {
    if (filterMode === "hypotheses") {
      return prioritizedNodes.filter((n) => n.node_type === "root_cause_hypothesis");
    }
    if (filterMode === "deployments") {
      return prioritizedNodes.filter((n) => n.node_type === "deployment");
    }
    if (filterMode === "errors") {
      return prioritizedNodes.filter((n) => n.node_type === "error_event");
    }
    if (filterMode === "health") {
      return prioritizedNodes.filter((n) => n.node_type === "health_event");
    }
    if (filterMode === "services") {
      return prioritizedNodes.filter((n) => n.node_type === "service");
    }
    if (filterMode === "events") {
      return prioritizedNodes.filter(
        (n) => n.node_type !== "service" && n.node_type !== "root_cause_hypothesis"
      );
    }
    if (filterMode === "causal_only") {
      const causalRels = new Set([
        "causes",
        "triggers_error",
        "propagates_to",
        "hypothesized_cause",
        "evidence_for",
      ]);
      const causalNodeIds = new Set();
      if (leadingHypothesisNode) causalNodeIds.add(leadingHypothesisNode.id);

      for (const e of deduplicatedEdges) {
        if (causalRels.has(e.relationship_type)) {
          causalNodeIds.add(e.source);
          causalNodeIds.add(e.target);
        }
      }
      return prioritizedNodes.filter((n) => causalNodeIds.has(n.id));
    }

    return prioritizedNodes;
  }, [prioritizedNodes, filterMode, leadingHypothesisNode, deduplicatedEdges]);

  // Visible nodes: limited by batch size (default 25)
  const visibleNodes = useMemo(() => {
    return filteredPrioritizedNodes.slice(0, visibleNodeLimit);
  }, [filteredPrioritizedNodes, visibleNodeLimit]);

  // Visible edges: strictly connecting visible nodes
  const visibleEdges = useMemo(() => {
    const visibleIdSet = new Set(visibleNodes.map((n) => n.id));
    return deduplicatedEdges.filter(
      (e) => visibleIdSet.has(e.source) && visibleIdSet.has(e.target)
    );
  }, [deduplicatedEdges, visibleNodes]);

  // Edge bundling: compute parallelIndex and parallelTotal for each edge
  const edgeBundleMap = useMemo(() => {
    const map = new Map();
    const groups = new Map();
    for (const edge of visibleEdges) {
      const key = [edge.source, edge.target].sort().join("---");
      if (!groups.has(key)) {
        groups.set(key, []);
      }
      groups.get(key).push(edge.id);
    }
    for (const edgeIds of groups.values()) {
      const total = edgeIds.length;
      edgeIds.forEach((id, idx) => {
        map.set(id, { index: idx, total });
      });
    }
    return map;
  }, [visibleEdges]);

  // Compute 4-column structured layout coordinates with generous spacing
  const { nodePositions, nodeColumns, columnDefs, svgWidth, svgHeight } = useMemo(() => {
    const pos = {};
    const cols = {};
    if (!visibleNodes || visibleNodes.length === 0) {
      return { nodePositions: pos, nodeColumns: cols, columnDefs: [], svgWidth: 1280, svgHeight: 520 };
    }

    const col0 = []; // Hypotheses
    const col1 = []; // Deployments
    const col2 = []; // Errors & Health
    const col3 = []; // Services

    for (const node of visibleNodes) {
      if (node.node_type === "root_cause_hypothesis") {
        col0.push(node);
      } else if (node.node_type === "deployment") {
        col1.push(node);
      } else if (node.node_type === "error_event" || node.node_type === "health_event") {
        col2.push(node);
      } else {
        col3.push(node);
      }
    }

    const allColumns = [
      { key: "hypotheses", title: "1. ROOT CAUSE HYPOTHESES", nodes: col0, colIdx: 0 },
      { key: "deployments", title: "2. DEPLOYMENTS", nodes: col1, colIdx: 1 },
      { key: "telemetry", title: "3. TELEMETRY & ERRORS", nodes: col2, colIdx: 2 },
      { key: "services", title: "4. SERVICE TOPOLOGY", nodes: col3, colIdx: 3 },
    ];

    const activeCols = allColumns.filter((c) => c.nodes.length > 0);
    const numCols = Math.max(activeCols.length, 1);
    const maxItemsInCol = Math.max(1, ...allColumns.map((c) => c.nodes.length));

    const totalWidth = 1280;
    const computedHeight = Math.max(540, Math.min(2600, maxItemsInCol * 88 + 140));

    const marginX = 140;
    const availableWidth = totalWidth - marginX * 2;
    const colStep = numCols > 1 ? availableWidth / (numCols - 1) : 0;

    activeCols.forEach((col, idx) => {
      const colX = numCols === 1 ? totalWidth / 2 : marginX + idx * colStep;
      col.x = colX;
      const count = col.nodes.length;
      col.nodes.forEach((node, rowIdx) => {
        const colY = 90 + (rowIdx + 0.5) * ((computedHeight - 130) / Math.max(count, 1));
        pos[node.id] = { x: colX, y: colY };
        cols[node.id] = col.colIdx;
      });
    });

    return {
      nodePositions: pos,
      nodeColumns: cols,
      columnDefs: activeCols,
      svgWidth: totalWidth,
      svgHeight: computedHeight,
    };
  }, [visibleNodes]);

  // Stable selection and expansion callbacks
  const handleSelectNode = useCallback((nodeId) => {
    setSelectedNodeId((prev) => (prev === nodeId ? null : nodeId));
    setSelectedEdgeId(null);
  }, []);

  const handleSelectEdge = useCallback((edgeId) => {
    setSelectedEdgeId((prev) => (prev === edgeId ? null : edgeId));
    setSelectedNodeId(null);
  }, []);

  const handleClearSelection = useCallback(() => {
    setSelectedNodeId(null);
    setSelectedEdgeId(null);
    setHoveredEdgeId(null);
  }, []);

  const handleExpand25 = useCallback(() => {
    setVisibleNodeLimit((prev) => prev + BATCH_INCREMENT);
  }, []);

  const handleShowAll = useCallback(() => {
    setVisibleNodeLimit(Number.MAX_SAFE_INTEGER);
  }, []);

  const handleCollapse = useCallback(() => {
    setVisibleNodeLimit(INITIAL_BATCH_SIZE);
  }, []);

  // Jump to / focus an evidence entity from anywhere in the archive
  const handleFocusEvidence = useCallback((evidenceId) => {
    const targetNode = deduplicatedNodes.find(
      (n) =>
        n.entity_id === evidenceId ||
        n.id === evidenceId ||
        (n.evidence_ids && n.evidence_ids.includes(evidenceId))
    );

    if (targetNode) {
      const targetIdx = filteredPrioritizedNodes.findIndex((n) => n.id === targetNode.id);
      if (targetIdx >= visibleNodeLimit) {
        setVisibleNodeLimit(Math.ceil((targetIdx + 1) / BATCH_INCREMENT) * BATCH_INCREMENT);
      }
      setSelectedNodeId(targetNode.id);
      setSelectedEdgeId(null);
    }
  }, [deduplicatedNodes, filteredPrioritizedNodes, visibleNodeLimit]);

  // Node connection lookup for selection highlighting
  const { connectedNodeIds, connectedEdgeIds } = useMemo(() => {
    const nodeIds = new Set();
    const edgeIds = new Set();

    if (selectedNodeId) {
      nodeIds.add(selectedNodeId);
      for (const edge of visibleEdges) {
        if (edge.source === selectedNodeId) {
          nodeIds.add(edge.target);
          edgeIds.add(edge.id);
        } else if (edge.target === selectedNodeId) {
          nodeIds.add(edge.source);
          edgeIds.add(edge.id);
        }
      }
    } else if (selectedEdgeId) {
      edgeIds.add(selectedEdgeId);
      const edge = visibleEdges.find((e) => e.id === selectedEdgeId);
      if (edge) {
        nodeIds.add(edge.source);
        nodeIds.add(edge.target);
      }
    }

    return { connectedNodeIds: nodeIds, connectedEdgeIds: edgeIds };
  }, [selectedNodeId, selectedEdgeId, visibleEdges]);

  const hasSelection = Boolean(selectedNodeId || selectedEdgeId);

  // Keyboard Escape listener to clear selection
  useEffect(() => {
    function handleKeyDown(e) {
      if (e.key === "Escape") {
        handleClearSelection();
      }
    }
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [handleClearSelection]);

  // Selected entities for inspection
  const selectedNode = selectedNodeId ? nodeMap.get(selectedNodeId) : null;
  const selectedEdge = selectedEdgeId
    ? deduplicatedEdges.find((e) => e.id === selectedEdgeId)
    : null;

  const totalEntities = filteredPrioritizedNodes.length;
  const visibleCount = visibleNodes.length;
  const hasMoreEntities = totalEntities > visibleCount;
  const isExpanded = visibleNodeLimit > INITIAL_BATCH_SIZE;

  if (loading && !graphData) {
    return (
      <section className="card causal-graph-section">
        <div className="causal-graph-loading">
          <div className="spinner" />
          <p>Loading incident investigation graph...</p>
        </div>
      </section>
    );
  }

  return (
    <section className="card causal-graph-section">
      {/* Header Row */}
      <div className="causal-graph-header">
        <div>
          <div className="eyebrow">
            INCIDENT CAUSAL GRAPH <span className="eyebrow-line" />
          </div>
          <h3>
            Investigation Causal Graph
            {totalEntities > 0 && (
              <span className="causal-count-tag">
                {visibleCount} of {totalEntities} Entities · {visibleEdges.length} Edges
              </span>
            )}
          </h3>
          <p>
            Directional causal relationships synthesized from operational events, verified service
            dependencies, and root-cause hypotheses. Temporal proximity alone does not create causal links.
          </p>
        </div>

        {/* Top Control Bar */}
        <div className="causal-header-controls">
          {graphData && (
            <div className="causal-meta-badges">
              {refreshSuccessNotice && (
                <span className="causal-refresh-success-pill" role="status">
                  ✓ {refreshSuccessNotice}
                </span>
              )}
              {graphData.is_cached ? (
                <span className="graph-persistence-badge saved" title={`Graph ID: ${graphData.graph_id}`}>
                  💾 Saved Snapshot ({graphData.updated_at ? new Date(graphData.updated_at).toLocaleTimeString() : "Persisted"})
                </span>
              ) : (
                <span className="graph-persistence-badge fresh">
                  ⚡ Freshly Generated
                </span>
              )}
              {graphData.analysis_mode && (
                <span className={`rca-mode-badge ${graphData.analysis_mode}`}>
                  {graphData.analysis_mode === "gemini_vertex_ai"
                    ? "✨ Gemini 2.5 Flash"
                    : "⚙️ Deterministic Fallback"}
                </span>
              )}
            </div>
          )}

          <button
            type="button"
            className={`action-btn-sm causal-refresh-btn ${refreshing ? "is-refreshing" : ""}`}
            onClick={() => loadGraph(true)}
            disabled={refreshing}
            title={refreshing ? "Investigation evidence is being analyzed..." : "Rebuild graph from active telemetry and update saved snapshot"}
            aria-busy={refreshing}
          >
            {refreshing ? (
              <>
                <span className="btn-spinner" aria-hidden="true" />
                <span>Analyzing Evidence...</span>
              </>
            ) : (
              <>
                <span className="refresh-icon" aria-hidden="true">↻</span>
                <span>Refresh Causal Graph</span>
              </>
            )}
          </button>
        </div>
      </div>

      {error && (
        <div className="notice error-notice causal-error-notice" role="alert" style={{ marginBottom: "16px" }}>
          <div className="causal-error-message">
            <span className="error-icon" aria-hidden="true">⚠️</span>
            <span>{error}</span>
          </div>
          <button
            type="button"
            className="action-btn-sm causal-retry-btn"
            onClick={() => loadGraph(true)}
            disabled={refreshing}
            title="Retry refreshing the causal graph"
          >
            ↻ Retry
          </button>
        </div>
      )}

      {/* Leading Hypothesis Callout */}
      {leadingHypothesisNode && !graphData?.insufficient_evidence && (
        <div
          className={`leading-hypothesis-banner ${
            selectedNodeId === leadingHypothesisNode.id ? "active-focus" : ""
          }`}
          onClick={() => handleSelectNode(leadingHypothesisNode.id)}
          role="button"
          tabIndex={0}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === " ") handleSelectNode(leadingHypothesisNode.id);
          }}
        >
          <div className="leading-banner-icon">🎯</div>
          <div className="leading-banner-content">
            <div className="leading-banner-top">
              <span className="leading-badge">LEADING ROOT CAUSE</span>
              <span className={`rca-confidence-badge ${(leadingHypothesisNode.metadata?.confidence || "medium").toLowerCase()}`}>
                {leadingHypothesisNode.metadata?.confidence || "High"} Confidence
              </span>
              <strong className="leading-svc">{leadingHypothesisNode.service}</strong>
            </div>
            <div className="leading-banner-label">{leadingHypothesisNode.label}</div>
          </div>
          <div className="leading-banner-action">
            {selectedNodeId === leadingHypothesisNode.id ? "Selected" : "Inspect in Graph →"}
          </div>
        </div>
      )}

      {/* Insufficient Evidence Notice */}
      {graphData?.insufficient_evidence && (
        <div className="insufficient-evidence-card" style={{ marginBottom: "16px" }}>
          <div className="insufficient-header">
            <span className="insufficient-icon">⚠️</span>
            <div>
              <h4>Insufficient evidence to determine root-cause causal links.</h4>
              <p className="muted">
                {graphData.summary ||
                  "Displaying known service dependencies and recorded operational events without inventing causal relationships."}
              </p>
            </div>
          </div>
        </div>
      )}

      {/* Filter and Legend Bar */}
      <div className="causal-toolbar">
        <div className="causal-filter-pills">
          <span className="toolbar-label">Filter:</span>
          <button
            type="button"
            className={`filter-chip ${filterMode === "all" ? "active" : ""}`}
            onClick={() => setFilterMode("all")}
          >
            All ({deduplicatedNodes.length})
          </button>
          <button
            type="button"
            className={`filter-chip ${filterMode === "causal_only" ? "active" : ""}`}
            onClick={() => setFilterMode("causal_only")}
          >
            Causal Path
          </button>
          {counts.hypotheses > 0 && (
            <button
              type="button"
              className={`filter-chip ${filterMode === "hypotheses" ? "active" : ""}`}
              onClick={() => setFilterMode("hypotheses")}
            >
              🎯 Hypotheses ({counts.hypotheses})
            </button>
          )}
          {counts.deployments > 0 && (
            <button
              type="button"
              className={`filter-chip ${filterMode === "deployments" ? "active" : ""}`}
              onClick={() => setFilterMode("deployments")}
            >
              🚀 Deployments ({counts.deployments})
            </button>
          )}
          {counts.errors > 0 && (
            <button
              type="button"
              className={`filter-chip ${filterMode === "errors" ? "active" : ""}`}
              onClick={() => setFilterMode("errors")}
            >
              💥 Errors ({counts.errors})
            </button>
          )}
          {counts.health > 0 && (
            <button
              type="button"
              className={`filter-chip ${filterMode === "health" ? "active" : ""}`}
              onClick={() => setFilterMode("health")}
            >
              💚 Health ({counts.health})
            </button>
          )}
          {counts.services > 0 && (
            <button
              type="button"
              className={`filter-chip ${filterMode === "services" ? "active" : ""}`}
              onClick={() => setFilterMode("services")}
            >
              ☁️ Services ({counts.services})
            </button>
          )}
        </div>

        {/* Legend & View Toggles */}
        <div className="causal-legend">
          <button
            type="button"
            className={`action-btn-sm ${showAllLabels ? "active" : ""}`}
            onClick={() => setShowAllLabels((prev) => !prev)}
            title="Toggle showing all relationship labels"
            style={{ fontSize: "11px", padding: "3px 8px" }}
          >
            {showAllLabels ? "🏷️ Labels: All" : "🏷️ Labels: Hover / Selected"}
          </button>

          {hasSelection && (
            <button
              type="button"
              className="action-btn-sm"
              onClick={handleClearSelection}
              title="Clear selection (or click canvas background / press Esc)"
              style={{ fontSize: "11px", padding: "3px 8px", color: "var(--brand-primary)" }}
            >
              ✕ Reset Focus
            </button>
          )}

          <span className="legend-item">
            <span className="legend-line verified" /> Verified
          </span>
          <span className="legend-item">
            <span className="legend-line configured" /> Configured
          </span>
          <span className="legend-item">
            <span className="legend-line inferred" /> Inferred
          </span>
          <span className="legend-item">
            <span className="legend-line neutral" /> Emitted
          </span>
        </div>
      </div>

      {/* Batch Expansion & Evidence Control Bar (Requirements 1 & 2) */}
      {(totalEntities > INITIAL_BATCH_SIZE || isExpanded) && (
        <div className="causal-pagination-bar">
          <div className="causal-pagination-info">
            <span className="causal-badge-count">
              Showing {visibleCount} of {totalEntities} entities
            </span>
            {hasMoreEntities && (
              <span className="causal-pagination-hint">
                Simplified view prioritized leading cause, deployment triggers, critical errors, and dependencies.
              </span>
            )}
          </div>

          <div className="causal-pagination-actions">
            {hasMoreEntities && (
              <>
                <button
                  type="button"
                  className="action-btn-sm causal-batch-btn"
                  onClick={handleExpand25}
                  title="Load next batch of 25 entities"
                >
                  + Expand 25 More
                </button>
                <button
                  type="button"
                  className="action-btn-sm causal-batch-btn highlight"
                  onClick={handleShowAll}
                  title="Render all entities simultaneously"
                >
                  Show All Evidence ({totalEntities})
                </button>
              </>
            )}
            {isExpanded && (
              <button
                type="button"
                className="action-btn-sm causal-batch-btn collapse"
                onClick={handleCollapse}
                title="Collapse back to simplified view with 25 nodes"
              >
                ◀ Collapse
              </button>
            )}
          </div>
        </div>
      )}

      {/* Main Graph Visualization Grid (SVG Canvas + Inspector Drawer) */}
      <div className="causal-canvas-container">
        {refreshing && (
          <div
            className="causal-refresh-indicator-bar"
            role="status"
            aria-live="polite"
          >
            <div className="refresh-indicator-left">
              <span className="refresh-pulse-dot" aria-hidden="true" />
              <span className="refresh-stage-text">
                {REFRESH_STAGES[refreshStageIndex]?.label || "Analyzing Evidence..."}
              </span>
            </div>
            <div className="refresh-indicator-right">
              <span className="refresh-stage-step">
                Stage {refreshStageIndex + 1} of {REFRESH_STAGES.length}
              </span>
              <div className="refresh-indicator-track" aria-hidden="true">
                <div
                  className="refresh-indicator-fill"
                  style={{ width: `${((refreshStageIndex + 1) / REFRESH_STAGES.length) * 100}%` }}
                />
              </div>
            </div>
          </div>
        )}

        {visibleNodes.length === 0 ? (
          <div className="timeline-empty" style={{ padding: "48px 0" }}>
            <p>No graph nodes match the current filter.</p>
            <button
              type="button"
              className="action-btn-sm"
              onClick={() => setFilterMode("all")}
              style={{ marginTop: "12px" }}
            >
              Reset filter
            </button>
          </div>
        ) : (
          <div className="causal-graph-layout">
            {/* SVG Visual Canvas */}
            <div className={`causal-svg-wrapper ${refreshing ? "is-refreshing" : ""}`}>
              <svg
                className="causal-svg"
                viewBox={`0 0 ${svgWidth} ${svgHeight}`}
                preserveAspectRatio="xMidYMid meet"
              >
                <GraphDefs />

                {/* Canvas background hit area to clear selection on empty space click */}
                <rect
                  className="causal-canvas-bg"
                  width={svgWidth}
                  height={svgHeight}
                  fill="transparent"
                  onClick={handleClearSelection}
                />

                {/* Column Section Headers */}
                {columnDefs.map((col) => (
                  <g key={col.key} transform={`translate(${col.x}, 35)`} style={{ pointerEvents: "none" }}>
                    <text
                      textAnchor="middle"
                      fill="var(--text-muted)"
                      fontSize="11"
                      fontWeight="700"
                      fontFamily="JetBrains Mono, monospace"
                      letterSpacing="0.8px"
                    >
                      {col.title} ({col.nodes.length})
                    </text>
                  </g>
                ))}

                {/* Render Directional Edges (Memoized) */}
                {visibleEdges.map((edge) => {
                  const sourcePos = nodePositions[edge.source];
                  const targetPos = nodePositions[edge.target];
                  if (!sourcePos || !targetPos) return null;

                  const sourceNode = nodeMap.get(edge.source);
                  const targetNode = nodeMap.get(edge.target);
                  const sourceCol = nodeColumns[edge.source] ?? 0;
                  const targetCol = nodeColumns[edge.target] ?? 0;
                  const sourceDim = {
                    w: sourceNode?.node_type === "root_cause_hypothesis" ? 220 : 190,
                    h: sourceNode?.node_type === "root_cause_hypothesis" ? 64 : 56,
                  };
                  const targetDim = {
                    w: targetNode?.node_type === "root_cause_hypothesis" ? 220 : 190,
                    h: targetNode?.node_type === "root_cause_hypothesis" ? 64 : 56,
                  };

                  const bundle = edgeBundleMap.get(edge.id) || { index: 0, total: 1 };
                  const isSelected = selectedEdgeId === edge.id;
                  const isNodeConnected = connectedEdgeIds.has(edge.id);
                  const isDimmed = hasSelection && !isSelected && !isNodeConnected;
                  const isHovered = hoveredEdgeId === edge.id;

                  return (
                    <GraphEdge
                      key={edge.id}
                      edge={edge}
                      sourcePos={sourcePos}
                      targetPos={targetPos}
                      sourceCol={sourceCol}
                      targetCol={targetCol}
                      sourceDim={sourceDim}
                      targetDim={targetDim}
                      parallelIndex={bundle.index}
                      parallelTotal={bundle.total}
                      isSelected={isSelected}
                      isNodeConnected={isNodeConnected}
                      isDimmed={isDimmed}
                      showAllLabels={showAllLabels}
                      isHovered={isHovered}
                      isRecentlyUpdated={recentlyUpdated.edgeIds.has(edge.id)}
                      onSelect={handleSelectEdge}
                      onHover={setHoveredEdgeId}
                    />
                  );
                })}

                {/* Render Nodes (Memoized) */}
                {visibleNodes.map((node) => {
                  const pos = nodePositions[node.id];
                  if (!pos) return null;

                  const isSelected = selectedNodeId === node.id;
                  const isDirectlyConnected = hasSelection && !isSelected && connectedNodeIds.has(node.id);
                  const isDimmed = hasSelection && !isSelected && !isDirectlyConnected;
                  const isLeading = node.is_leading_hypothesis;
                  const isIncidentService = node.service === incidentService;

                  return (
                    <GraphNode
                      key={node.id}
                      node={node}
                      x={pos.x}
                      y={pos.y}
                      isSelected={isSelected}
                      isLeading={isLeading}
                      isIncidentService={isIncidentService}
                      isDirectlyConnected={isDirectlyConnected}
                      isDimmed={isDimmed}
                      isRecentlyUpdated={recentlyUpdated.nodeIds.has(node.id)}
                      onSelect={handleSelectNode}
                    />
                  );
                })}
              </svg>
            </div>

            {/* Inspector Details Panel (Drawer) */}
            <aside className="causal-inspector-panel">
              {selectedNode ? (
                <div className="inspector-content">
                  <div className="inspector-header">
                    <span className="inspector-type-pill">
                      {NODE_ICONS[selectedNode.node_type]} {NODE_TYPE_LABELS[selectedNode.node_type]}
                    </span>
                    <button
                      type="button"
                      className="inspector-close-btn"
                      onClick={handleClearSelection}
                      title="Clear selection"
                    >
                      ✕
                    </button>
                  </div>

                  <h4 className="inspector-title">{selectedNode.label}</h4>

                  {selectedNode.is_leading_hypothesis && (
                    <div className="inspector-leading-tag">
                      🎯 Top-Ranked Root Cause Hypothesis
                    </div>
                  )}

                  {/* Fact vs Inferred distinction */}
                  <div className="inspector-classification-box">
                    <span className="class-label">Classification:</span>
                    {selectedNode.node_type === "root_cause_hypothesis" ? (
                      <span className="class-val inferred">
                        Synthesized Hypothesis (Inferred from Telemetry)
                      </span>
                    ) : (
                      <span className="class-val fact">
                        Observed Telemetry Fact ({selectedNode.service || "System"})
                      </span>
                    )}
                  </div>

                  <div className="inspector-metadata-grid">
                    <div className="inspector-meta-row">
                      <span className="meta-key">Node ID:</span>
                      <code className="meta-val">{selectedNode.id}</code>
                    </div>

                    {selectedNode.entity_id && (
                      <div className="inspector-meta-row">
                        <span className="meta-key">Entity ID:</span>
                        <code className="meta-val">{selectedNode.entity_id}</code>
                      </div>
                    )}

                    {selectedNode.service && (
                      <div className="inspector-meta-row">
                        <span className="meta-key">Service:</span>
                        <strong className="meta-val text-blue">{selectedNode.service}</strong>
                      </div>
                    )}

                    {selectedNode.timestamp && (
                      <div className="inspector-meta-row">
                        <span className="meta-key">Timestamp:</span>
                        <span className="meta-val">{formatTimestamp(selectedNode.timestamp)}</span>
                      </div>
                    )}
                  </div>

                  {/* Node Metadata specifics */}
                  {selectedNode.metadata && Object.keys(selectedNode.metadata).length > 0 && (
                    <div className="inspector-section">
                      <h5>Node Metadata</h5>
                      <div className="inspector-meta-pills">
                        {Object.entries(selectedNode.metadata).map(([k, v]) => {
                          if (typeof v === "object") return null;
                          return (
                            <div key={k} className="meta-pill-item">
                              <span className="k">{k.replace(/_/g, " ")}:</span>
                              <span className="v">{String(v)}</span>
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  )}

                  {/* Supporting Evidence References */}
                  {selectedNode.evidence_ids?.length > 0 && (
                    <div className="inspector-section">
                      <h5>Supporting Telemetry Evidence ({selectedNode.evidence_ids.length})</h5>
                      <div className="inspector-evidence-list">
                        {selectedNode.evidence_ids.map((eid) => {
                          const ev = evidenceMap.get(eid);
                          if (!ev) {
                            return (
                              <div key={eid} className="evidence-ref-card unavailable">
                                <span className="ev-icon">⚠️</span>
                                <div className="ev-info">
                                  <strong>{eid}</strong>
                                  <p className="muted">Evidence reference preserved; raw event not loaded.</p>
                                </div>
                              </div>
                            );
                          }

                          return (
                            <div key={eid} className="evidence-ref-card">
                              <div className="ev-card-top">
                                <span className="ev-type-badge">
                                  {ev.event_type === "deployment"
                                    ? "🚀 Deployment"
                                    : ev.event_type === "error_log"
                                    ? "💥 Error Log"
                                    : "💚 Health Check"}
                                </span>
                                <span className="ev-time">{formatTimestamp(ev.timestamp)}</span>
                              </div>
                              <p className="ev-summary">{ev.summary}</p>
                              <div className="ev-svc">Service: <strong>{ev.service}</strong></div>
                              {ev.raw_payload && Object.keys(ev.raw_payload).length > 0 && (
                                <details className="ev-details">
                                  <summary>Inspect payload</summary>
                                  <pre>{formatValue(ev.raw_payload)}</pre>
                                </details>
                              )}
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  )}
                </div>
              ) : selectedEdge ? (
                <div className="inspector-content">
                  <div className="inspector-header">
                    <span className="inspector-type-pill edge">
                      Relationship Link
                    </span>
                    <button
                      type="button"
                      className="inspector-close-btn"
                      onClick={handleClearSelection}
                      title="Clear selection"
                    >
                      ✕
                    </button>
                  </div>

                  <h4 className="inspector-title">
                    {(selectedEdge.relationship_type || "relates_to").replace(/_/g, " ").toUpperCase()}
                  </h4>

                  {/* Verification Status Banner */}
                  <div className="edge-verification-banner">
                    <span className={`status-pill ${selectedEdge.verification_status}`}>
                      {(selectedEdge.verification_status || "configured").toUpperCase()}
                    </span>
                    <span className="edge-conf">
                      {selectedEdge.confidence ? `${selectedEdge.confidence.toUpperCase()} CONFIDENCE` : ""}
                    </span>
                  </div>

                  {/* Fact vs Inferred distinction */}
                  <div className="inspector-classification-box">
                    <span className="class-label">Epistemological Status:</span>
                    {selectedEdge.verification_status === "verified" ? (
                      <span className="class-val fact">
                        Observed & Verified Dependency (Active runtime traffic)
                      </span>
                    ) : selectedEdge.verification_status === "inferred" ? (
                      <span className="class-val inferred">
                        Inferred Causal Link (Evidence-backed hypothesis; temporal proximity alone rejected)
                      </span>
                    ) : (
                      <span className="class-val configured">
                        Configured Architecture (Service configuration record)
                      </span>
                    )}
                  </div>

                  <div className="inspector-metadata-grid">
                    <div className="inspector-meta-row">
                      <span className="meta-key">Source Entity:</span>
                      <strong className="meta-val">{nodeMap.get(selectedEdge.source)?.label || selectedEdge.source}</strong>
                    </div>

                    <div className="inspector-meta-row">
                      <span className="meta-key">Target Entity:</span>
                      <strong className="meta-val">{nodeMap.get(selectedEdge.target)?.label || selectedEdge.target}</strong>
                    </div>
                  </div>

                  {/* Explanation */}
                  <div className="inspector-section">
                    <h5>Causal Explanation</h5>
                    <p className="edge-explanation-text">{selectedEdge.explanation}</p>
                  </div>

                  {/* Supporting Evidence IDs */}
                  {selectedEdge.supporting_evidence_ids?.length > 0 && (
                    <div className="inspector-section">
                      <h5>Supporting Evidence ({selectedEdge.supporting_evidence_ids.length})</h5>
                      <div className="inspector-evidence-list">
                        {selectedEdge.supporting_evidence_ids.map((eid) => {
                          const ev = evidenceMap.get(eid);
                          return (
                            <div key={eid} className="evidence-ref-card">
                              <div className="ev-card-top">
                                <strong>{eid}</strong>
                                {ev && <span className="ev-time">{formatTimestamp(ev.timestamp)}</span>}
                              </div>
                              {ev ? (
                                <>
                                  <p className="ev-summary">{ev.summary}</p>
                                  {ev.raw_payload && (
                                    <details className="ev-details">
                                      <summary>Inspect evidence payload</summary>
                                      <pre>{formatValue(ev.raw_payload)}</pre>
                                    </details>
                                  )}
                                </>
                              ) : (
                                <p className="muted">Evidence reference available via audit log.</p>
                              )}
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  )}
                </div>
              ) : (
                /* Fallback to Evidence Overview preserving access to all evidence */
                <EvidenceOverviewDrawer
                  evidenceDetails={graphData?.evidence_details}
                  evidenceReferences={graphData?.evidence_references}
                  totalEntities={totalEntities}
                  visibleCount={visibleCount}
                  leadingHypothesis={graphData?.leading_hypothesis}
                  onFocusEvidence={handleFocusEvidence}
                />
              )}
            </aside>
          </div>
        )}
      </div>
    </section>
  );
}
