import { useState, useMemo, useEffect } from "react";
import { request, api } from "../firebase.js";

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

export default function CausalGraphView({ incidentId, incidentService }) {
  const [graphData, setGraphData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");
  const [selectedNodeId, setSelectedNodeId] = useState(null);
  const [selectedEdgeId, setSelectedEdgeId] = useState(null);
  const [filterMode, setFilterMode] = useState("all"); // 'all' | 'causal_only' | 'services' | 'events'

  // Fetch or retrieve persisted graph
  async function loadGraph(forceRefresh = false) {
    if (forceRefresh) {
      setRefreshing(true);
    } else {
      setLoading(true);
    }
    setError("");

    try {
      let data;
      if (forceRefresh) {
        data = await api(`/incidents/${encodeURIComponent(incidentId)}/causal-graph/refresh`, {});
      } else {
        data = await request(`/incidents/${encodeURIComponent(incidentId)}/causal-graph`);
      }
      setGraphData(data);
      // Auto-select leading hypothesis if available and nothing currently selected
      if (!selectedNodeId && data?.leading_hypothesis_id) {
        setSelectedNodeId(data.leading_hypothesis_id);
      }
    } catch (err) {
      setError(err.message || "Failed to load incident investigation graph.");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }

  useEffect(() => {
    if (incidentId) {
      loadGraph(false);
    }
  }, [incidentId]);

  // Map nodes and evidence for fast lookups
  const nodeMap = useMemo(() => {
    const map = new Map();
    if (graphData?.nodes) {
      for (const n of graphData.nodes) {
        map.set(n.id, n);
      }
    }
    return map;
  }, [graphData]);

  const evidenceMap = useMemo(() => {
    const map = new Map();
    if (graphData?.evidence_references) {
      for (const ev of graphData.evidence_references) {
        map.set(ev.evidence_id, ev);
      }
    }
    return map;
  }, [graphData]);

  // Filtered nodes and edges based on filterMode
  const { filteredNodes, filteredEdges } = useMemo(() => {
    if (!graphData?.nodes) return { filteredNodes: [], filteredEdges: [] };

    const nodes = graphData.nodes;
    const edges = graphData.edges || [];

    if (filterMode === "services") {
      const fn = nodes.filter((n) => n.node_type === "service");
      const validIds = new Set(fn.map((n) => n.id));
      const fe = edges.filter((e) => validIds.has(e.source) && validIds.has(e.target));
      return { filteredNodes: fn, filteredEdges: fe };
    }

    if (filterMode === "events") {
      const fn = nodes.filter((n) => n.node_type !== "service" && n.node_type !== "root_cause_hypothesis");
      const validIds = new Set(fn.map((n) => n.id));
      const fe = edges.filter((e) => validIds.has(e.source) && validIds.has(e.target));
      return { filteredNodes: fn, filteredEdges: fe };
    }

    if (filterMode === "causal_only") {
      const causalRels = new Set([
        "causes",
        "triggers_error",
        "propagates_to",
        "hypothesized_cause",
        "evidence_for",
      ]);
      const fe = edges.filter((e) => causalRels.has(e.relationship_type));
      const involvedIds = new Set();
      fe.forEach((e) => {
        involvedIds.add(e.source);
        involvedIds.add(e.target);
      });
      // Always include leading hypothesis
      if (graphData.leading_hypothesis_id) {
        involvedIds.add(graphData.leading_hypothesis_id);
      }
      const fn = nodes.filter((n) => involvedIds.has(n.id));
      return { filteredNodes: fn.length ? fn : nodes, filteredEdges: fe.length ? fe : edges };
    }

    return { filteredNodes: nodes, filteredEdges: edges };
  }, [graphData, filterMode]);

  // Compute 4-column structured layout coordinates
  const { nodePositions, svgWidth, svgHeight } = useMemo(() => {
    const pos = {};
    if (!filteredNodes || filteredNodes.length === 0) {
      return { nodePositions: pos, svgWidth: 900, svgHeight: 460 };
    }

    // Partition nodes into 4 semantic columns:
    // Col 0: Hypotheses
    // Col 1: Deployments & Triggers
    // Col 2: Errors & Health events
    // Col 3: Services (upstream and downstream)
    const col0 = []; // Hypotheses
    const col1 = []; // Deployments
    const col2 = []; // Errors & Health
    const col3 = []; // Services

    for (const node of filteredNodes) {
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

    // Determine active columns
    const columns = [
      { key: "hypotheses", nodes: col0, label: "Hypotheses" },
      { key: "deployments", nodes: col1, label: "Deployments / Triggers" },
      { key: "telemetry", nodes: col2, label: "Observed Telemetry" },
      { key: "services", nodes: col3, label: "Service Topology" },
    ];

    const activeCols = columns.filter((c) => c.nodes.length > 0);
    const numCols = Math.max(activeCols.length, 1);
    const maxItemsInCol = Math.max(1, ...columns.map((c) => c.nodes.length));

    const totalWidth = 960;
    const itemHeight = 110;
    const computedHeight = Math.max(500, maxItemsInCol * itemHeight + 100);

    // X coordinates distribution
    const colSpacing = (totalWidth - 260) / Math.max(numCols - 1, 1);

    activeCols.forEach((col, colIdx) => {
      const colX = numCols === 1 ? totalWidth / 2 : 130 + colIdx * colSpacing;
      const count = col.nodes.length;
      col.nodes.forEach((node, rowIdx) => {
        const colY = 90 + (rowIdx + 0.5) * ((computedHeight - 140) / Math.max(count, 1));
        pos[node.id] = { x: colX, y: colY };
      });
    });

    return { nodePositions: pos, svgWidth: totalWidth, svgHeight: computedHeight };
  }, [filteredNodes]);

  // Selected entities
  const selectedNode = selectedNodeId ? nodeMap.get(selectedNodeId) : null;
  const selectedEdge = selectedEdgeId
    ? graphData?.edges?.find((e) => e.id === selectedEdgeId)
    : null;

  // Leading hypothesis helper
  const leadingHypothesisNode = useMemo(() => {
    if (!graphData?.nodes) return null;
    return (
      graphData.nodes.find((n) => n.id === graphData.leading_hypothesis_id) ||
      graphData.nodes.find((n) => n.is_leading_hypothesis) ||
      null
    );
  }, [graphData]);

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
            {graphData?.nodes?.length > 0 && (
              <span className="causal-count-tag">
                {graphData.nodes.length} Nodes · {graphData.edges?.length || 0} Edges
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
            className="action-btn-sm causal-refresh-btn"
            onClick={() => loadGraph(true)}
            disabled={refreshing}
            title="Rebuild graph from active telemetry and update saved snapshot"
          >
            {refreshing ? "Rebuilding Graph..." : "↻ Refresh Causal Graph"}
          </button>
        </div>
      </div>

      {error && (
        <div className="notice error-notice" role="alert" style={{ marginBottom: "16px" }}>
          {error}
        </div>
      )}

      {/* Leading Hypothesis Callout */}
      {leadingHypothesisNode && !graphData?.insufficient_evidence && (
        <div
          className={`leading-hypothesis-banner ${
            selectedNodeId === leadingHypothesisNode.id ? "active-focus" : ""
          }`}
          onClick={() => {
            setSelectedNodeId(leadingHypothesisNode.id);
            setSelectedEdgeId(null);
          }}
          role="button"
          tabIndex={0}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === " ") setSelectedNodeId(leadingHypothesisNode.id);
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
          <span className="toolbar-label">View:</span>
          <button
            type="button"
            className={`filter-chip ${filterMode === "all" ? "active" : ""}`}
            onClick={() => setFilterMode("all")}
          >
            All Entities ({graphData?.nodes?.length || 0})
          </button>
          <button
            type="button"
            className={`filter-chip ${filterMode === "causal_only" ? "active" : ""}`}
            onClick={() => setFilterMode("causal_only")}
          >
            Causal Path Only
          </button>
          <button
            type="button"
            className={`filter-chip ${filterMode === "services" ? "active" : ""}`}
            onClick={() => setFilterMode("services")}
          >
            Services Only
          </button>
          <button
            type="button"
            className={`filter-chip ${filterMode === "events" ? "active" : ""}`}
            onClick={() => setFilterMode("events")}
          >
            Events Only
          </button>
        </div>

        {/* Legend */}
        <div className="causal-legend">
          <span className="legend-item">
            <span className="legend-line verified" /> Verified Dependency
          </span>
          <span className="legend-item">
            <span className="legend-line configured" /> Configured Dependency
          </span>
          <span className="legend-item">
            <span className="legend-line inferred" /> Inferred Causal Link
          </span>
          <span className="legend-item">
            <span className="legend-line neutral" /> Emitted / Observed
          </span>
        </div>
      </div>

      {/* Main Graph Visualization Grid (SVG Canvas + Inspector Drawer) */}
      <div className="causal-canvas-container">
        {filteredNodes.length === 0 ? (
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
            <div className="causal-svg-wrapper">
              <svg
                className="causal-svg"
                viewBox={`0 0 ${svgWidth} ${svgHeight}`}
                preserveAspectRatio="xMidYMid meet"
              >
                <defs>
                  {/* Verified Arrow Marker */}
                  <marker
                    id="causal-arrow-verified"
                    viewBox="0 0 10 10"
                    refX="9"
                    refY="5"
                    markerWidth="6"
                    markerHeight="6"
                    orient="auto-start-reverse"
                  >
                    <path d="M 0 1 L 10 5 L 0 9 z" fill="#66d6a4" />
                  </marker>

                  {/* Configured Arrow Marker */}
                  <marker
                    id="causal-arrow-configured"
                    viewBox="0 0 10 10"
                    refX="9"
                    refY="5"
                    markerWidth="6"
                    markerHeight="6"
                    orient="auto-start-reverse"
                  >
                    <path d="M 0 1 L 10 5 L 0 9 z" fill="#6e9cff" />
                  </marker>

                  {/* Inferred Causal Arrow Marker */}
                  <marker
                    id="causal-arrow-inferred"
                    viewBox="0 0 10 10"
                    refX="9"
                    refY="5"
                    markerWidth="6"
                    markerHeight="6"
                    orient="auto-start-reverse"
                  >
                    <path d="M 0 1 L 10 5 L 0 9 z" fill="#f5bd67" />
                  </marker>

                  {/* Neutral / Telemetry Arrow Marker */}
                  <marker
                    id="causal-arrow-neutral"
                    viewBox="0 0 10 10"
                    refX="9"
                    refY="5"
                    markerWidth="6"
                    markerHeight="6"
                    orient="auto-start-reverse"
                  >
                    <path d="M 0 1 L 10 5 L 0 9 z" fill="#71819d" />
                  </marker>

                  {/* Glow filter for leading hypothesis */}
                  <filter id="glow-gold" x="-20%" y="-20%" width="140%" height="140%">
                    <feDropShadow dx="0" dy="0" stdDeviation="6" floodColor="#f5bd67" floodOpacity="0.45" />
                  </filter>
                  <filter id="glow-cyan" x="-20%" y="-20%" width="140%" height="140%">
                    <feDropShadow dx="0" dy="0" stdDeviation="6" floodColor="#6be2dc" floodOpacity="0.5" />
                  </filter>
                </defs>

                {/* Render Directional Edges */}
                {filteredEdges.map((edge) => {
                  const p1 = nodePositions[edge.source];
                  const p2 = nodePositions[edge.target];
                  if (!p1 || !p2) return null;

                  const isSelected = selectedEdgeId === edge.id;
                  const isNodeConnected =
                    selectedNodeId && (edge.source === selectedNodeId || edge.target === selectedNodeId);

                  const isCausalRel =
                    edge.relationship_type === "causes" ||
                    edge.relationship_type === "triggers_error" ||
                    edge.relationship_type === "propagates_to" ||
                    edge.relationship_type === "hypothesized_cause";

                  const markerId =
                    edge.verification_status === "verified"
                      ? "url(#causal-arrow-verified)"
                      : edge.verification_status === "inferred" || isCausalRel
                      ? "url(#causal-arrow-inferred)"
                      : edge.verification_status === "configured"
                      ? "url(#causal-arrow-configured)"
                      : "url(#causal-arrow-neutral)";

                  const strokeColor = isSelected
                    ? "#6be2dc"
                    : isNodeConnected
                    ? "#ffffff"
                    : edge.verification_status === "verified"
                    ? "#66d6a4"
                    : edge.verification_status === "inferred" || isCausalRel
                    ? "#f5bd67"
                    : edge.verification_status === "configured"
                    ? "#6e9cff"
                    : "#4b5d7e";

                  const strokeDash =
                    edge.verification_status === "configured"
                      ? "6 4"
                      : edge.verification_status === "inferred" || isCausalRel
                      ? "5 3"
                      : edge.relationship_type === "occurred_on"
                      ? "2 4"
                      : "none";

                  // Smooth curve routing
                  const dx = p2.x - p1.x;
                  const dy = p2.y - p1.y;
                  const cx1 = p1.x + dx * 0.45;
                  const cy1 = p1.y;
                  const cx2 = p1.x + dx * 0.55;
                  const cy2 = p2.y;

                  const pathD = `M ${p1.x} ${p1.y} C ${cx1} ${cy1}, ${cx2} ${cy2}, ${p2.x} ${p2.y}`;
                  const midX = (p1.x + p2.x) / 2;
                  const midY = (p1.y + p2.y) / 2 - 8;

                  const relLabel = edge.relationship_type.replace(/_/g, " ");

                  return (
                    <g
                      key={edge.id}
                      className={`causal-edge-group ${isSelected ? "selected" : ""} ${
                        isNodeConnected ? "connected-focus" : ""
                      }`}
                      onClick={() => {
                        setSelectedEdgeId(edge.id);
                        setSelectedNodeId(null);
                      }}
                      style={{ cursor: "pointer" }}
                    >
                      {/* Click target halo */}
                      <path d={pathD} fill="none" stroke="transparent" strokeWidth="18" />

                      {/* Visible Edge Path */}
                      <path
                        d={pathD}
                        fill="none"
                        stroke={strokeColor}
                        strokeWidth={isSelected ? 3.5 : isNodeConnected ? 2.5 : 1.6}
                        strokeDasharray={strokeDash}
                        markerEnd={markerId}
                        opacity={isSelected || isNodeConnected ? 1 : 0.72}
                      />

                      {/* Relationship Pill */}
                      <rect
                        x={midX - Math.max(34, relLabel.length * 3.6)}
                        y={midY - 9}
                        width={Math.max(68, relLabel.length * 7.2)}
                        height="18"
                        rx="4"
                        fill="#0c111c"
                        stroke={strokeColor}
                        strokeWidth={isSelected ? 1.5 : 1}
                        opacity="0.95"
                      />
                      <text
                        x={midX}
                        y={midY + 3.5}
                        textAnchor="middle"
                        fill={isSelected ? "#6be2dc" : "#d2dceb"}
                        fontSize="9.5"
                        fontWeight="600"
                        fontFamily="DM Mono, monospace"
                      >
                        {relLabel}
                      </text>
                    </g>
                  );
                })}

                {/* Render Nodes */}
                {filteredNodes.map((node) => {
                  const pos = nodePositions[node.id];
                  if (!pos) return null;

                  const isSelected = selectedNodeId === node.id;
                  const isLeading = node.is_leading_hypothesis;
                  const isIncidentService = node.service === incidentService;

                  const icon = NODE_ICONS[node.node_type] || "📦";

                  // Width and Height of Node Box
                  const w = node.node_type === "root_cause_hypothesis" ? 220 : 180;
                  const h = node.node_type === "root_cause_hypothesis" ? 64 : 54;
                  const rx = 8;

                  let borderColor = "#263148";
                  let bgFill = "#111725";

                  if (isSelected) {
                    borderColor = "#6be2dc";
                    bgFill = "#18263d";
                  } else if (isLeading) {
                    borderColor = "#f5bd67";
                    bgFill = "#261d15";
                  } else if (node.node_type === "deployment") {
                    borderColor = "#6e9cff";
                    bgFill = "#121a2c";
                  } else if (node.node_type === "error_event") {
                    borderColor = "#ff7e8b";
                    bgFill = "#28151b";
                  } else if (node.node_type === "service" && isIncidentService) {
                    borderColor = "#6be2dc";
                    bgFill = "#132332";
                  }

                  const filterEffect = isLeading
                    ? "url(#glow-gold)"
                    : isSelected
                    ? "url(#glow-cyan)"
                    : "none";

                  return (
                    <g
                      key={node.id}
                      className={`causal-node-group ${isSelected ? "selected" : ""} ${
                        isLeading ? "leading-node" : ""
                      }`}
                      transform={`translate(${pos.x - w / 2}, ${pos.y - h / 2})`}
                      onClick={() => {
                        setSelectedNodeId(node.id);
                        setSelectedEdgeId(null);
                      }}
                      style={{ cursor: "pointer" }}
                    >
                      {/* Node Box */}
                      <rect
                        width={w}
                        height={h}
                        rx={rx}
                        fill={bgFill}
                        stroke={borderColor}
                        strokeWidth={isSelected ? 2.5 : isLeading ? 2 : 1.2}
                        filter={filterEffect}
                      />

                      {/* Header row: Icon & Type Tag */}
                      <text x="12" y="20" fontSize="14">
                        {icon}
                      </text>

                      <text
                        x="34"
                        y="18"
                        fill="#8d9ab3"
                        fontSize="9"
                        fontWeight="600"
                        fontFamily="DM Mono, monospace"
                      >
                        {isLeading
                          ? "LEADING CAUSE (RANK #1)"
                          : NODE_TYPE_LABELS[node.node_type]?.toUpperCase() || "NODE"}
                      </text>

                      {/* Node Label / Title */}
                      <text
                        x="12"
                        y="36"
                        fill="#e9edf6"
                        fontSize="11.5"
                        fontWeight="600"
                        fontFamily="DM Sans, sans-serif"
                      >
                        {node.label.length > 25 ? `${node.label.slice(0, 24)}…` : node.label}
                      </text>

                      {/* Subtitle / Service / Timestamp */}
                      <text
                        x="12"
                        y="49"
                        fill="#74829e"
                        fontSize="9"
                        fontFamily="DM Mono, monospace"
                      >
                        {node.service ? `svc: ${node.service}` : node.timestamp ? formatTimestamp(node.timestamp).split(",")[0] : ""}
                      </text>
                    </g>
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
                      onClick={() => setSelectedNodeId(null)}
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
                                  <p className="muted">Evidence reference no longer available in store.</p>
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
                      onClick={() => setSelectedEdgeId(null)}
                      title="Clear selection"
                    >
                      ✕
                    </button>
                  </div>

                  <h4 className="inspector-title">
                    {selectedEdge.relationship_type.replace(/_/g, " ").toUpperCase()}
                  </h4>

                  {/* Verification Status Banner */}
                  <div className="edge-verification-banner">
                    <span className={`status-pill ${selectedEdge.verification_status}`}>
                      {selectedEdge.verification_status.toUpperCase()}
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
                <div className="inspector-empty">
                  <div className="inspector-empty-icon">👆</div>
                  <h4>Inspect Graph Entities</h4>
                  <p className="muted">
                    Click any service node, deployment, error event, or directional link to examine
                    its causal rationale, timestamps, and supporting evidence.
                  </p>
                </div>
              )}
            </aside>
          </div>
        )}
      </div>
    </section>
  );
}
