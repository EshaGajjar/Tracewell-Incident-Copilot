import { useEffect, useState, useMemo } from "react";
import { request, api } from "../firebase.js";

const SERVICE_TYPE_ICONS = {
  cloud_run: "☁️",
  microservice: "📦",
  database: "🗄️",
  cache: "⚡",
  event_broker: "📨",
  external_api: "🌐",
  storage: "💾",
};

export default function ServiceDependencyView({
  initialSelectedService = null,
  onOpenIncident = null,
}) {
  const [services, setServices] = useState([]);
  const [dependencies, setDependencies] = useState([]);
  const [selectedService, setSelectedService] = useState(initialSelectedService);
  const [selectedDependency, setSelectedDependency] = useState(null);
  const [impactData, setImpactData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [impactLoading, setImpactLoading] = useState(false);
  const [error, setError] = useState("");
  const [envFilter, setEnvFilter] = useState("all");
  const [showAddModal, setShowAddModal] = useState(false);
  const [discovering, setDiscovering] = useState(false);
  const [discoveryNote, setDiscoveryNote] = useState("");

  // Add dependency form state
  const [newDep, setNewDep] = useState({
    source: "",
    target: "",
    relationship_type: "sync_http",
    evidence: "",
    confidence: "high",
    verification_status: "configured",
    environment: "production",
  });

  async function loadData() {
    setLoading(true);
    setError("");
    try {
      const data = await request("/dependencies");
      setServices(Array.isArray(data.services) ? data.services : []);
      setDependencies(Array.isArray(data.dependencies) ? data.dependencies : []);
      if (!selectedService && data.services?.length > 0) {
        setSelectedService(initialSelectedService || data.services[0].name);
      }
    } catch (err) {
      setError(err.message || "Failed to load service dependencies.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    loadData();
  }, []);

  // When selected service changes, fetch downstream impact and direct dependencies
  useEffect(() => {
    if (!selectedService) {
      setImpactData(null);
      return;
    }

    let active = true;
    async function fetchImpact() {
      setImpactLoading(true);
      try {
        const impact = await request(
          `/services/${encodeURIComponent(selectedService)}/downstream-impact`
        );
        if (active) {
          setImpactData(impact);
        }
      } catch (err) {
        if (active) {
          console.warn("Could not fetch downstream impact:", err);
          setImpactData(null);
        }
      } finally {
        if (active) setImpactLoading(false);
      }
    }

    fetchImpact();
    return () => {
      active = false;
    };
  }, [selectedService]);

  async function handleAddDependency(e) {
    e.preventDefault();
    if (!newDep.source || !newDep.target || !newDep.evidence) {
      alert("Please fill in source, target, and evidence.");
      return;
    }
    if (newDep.source === newDep.target) {
      alert("Source and target services cannot be the same.");
      return;
    }

    try {
      await api("/dependencies", newDep);
      setShowAddModal(false);
      setNewDep({
        source: "",
        target: "",
        relationship_type: "sync_http",
        evidence: "",
        confidence: "high",
        verification_status: "configured",
        environment: "production",
      });
      await loadData();
    } catch (err) {
      alert(`Failed to add dependency: ${err.message}`);
    }
  }

  async function handleTriggerDiscovery() {
    setDiscovering(true);
    setDiscoveryNote("");
    try {
      const res = await api("/dependencies/discover", {});
      setDiscoveryNote(
        `Discovery finished: ${res.updated_relationships || 0} relationship(s) verified from active telemetry.`
      );
      await loadData();
    } catch (err) {
      setDiscoveryNote(`Discovery error: ${err.message}`);
    } finally {
      setDiscovering(false);
    }
  }

  // Filtered services and dependencies by environment
  const filteredServices = useMemo(() => {
    if (envFilter === "all") return services;
    return services.filter((s) => s.environment === envFilter);
  }, [services, envFilter]);

  const filteredDependencies = useMemo(() => {
    if (envFilter === "all") return dependencies;
    return dependencies.filter((d) => d.environment === envFilter);
  }, [dependencies, envFilter]);

  // Set of service names impacted by the currently selected service
  const impactedServiceMap = useMemo(() => {
    const map = new Map();
    if (impactData?.impacted_services) {
      for (const item of impactData.impacted_services) {
        map.set(item.service_name, item);
      }
    }
    return map;
  }, [impactData]);

  // Upstream dependencies of the selected service
  const upstreamDependencies = useMemo(() => {
    if (!selectedService) return [];
    return dependencies.filter((d) => d.source === selectedService);
  }, [dependencies, selectedService]);

  // Downstream dependents of the selected service
  const downstreamDependencies = useMemo(() => {
    if (!selectedService) return [];
    return dependencies.filter((d) => d.target === selectedService);
  }, [dependencies, selectedService]);

  // Calculate layout coordinates for SVG node representation
  const nodePositions = useMemo(() => {
    const pos = {};
    const total = filteredServices.length;
    if (total === 0) return pos;

    // Group services into tiers:
    // Tier 0: Gateways / Ingress (e.g. order-service)
    // Tier 1: Core microservices (e.g. payment-api, auth-service, notification-service)
    // Tier 2: Databases and Caches (e.g. payment-db, payment-cache, firestore)
    const tier0 = [];
    const tier1 = [];
    const tier2 = [];

    for (const svc of filteredServices) {
      const type = svc.service_type;
      if (type === "database" || type === "cache" || type === "storage") {
        tier2.push(svc);
      } else if (svc.name.includes("order") || svc.name.includes("gateway")) {
        tier0.push(svc);
      } else {
        tier1.push(svc);
      }
    }

    const tiers = [tier0, tier1, tier2];
    const width = 860;
    const height = 480;

    tiers.forEach((tierServices, tierIdx) => {
      const x = 140 + tierIdx * 290;
      const count = tierServices.length;
      tierServices.forEach((svc, idx) => {
        const y = 80 + (idx + 0.5) * ((height - 120) / Math.max(count, 1));
        pos[svc.name] = { x, y };
      });
    });

    return pos;
  }, [filteredServices]);

  const verifiedCount = dependencies.filter(
    (d) => d.verification_status === "verified"
  ).length;
  const configuredCount = dependencies.filter(
    (d) => d.verification_status === "configured"
  ).length;

  return (
    <div className="dependency-view">
      {/* Header Row */}
      <section className="welcome-row">
        <div>
          <div className="eyebrow">
            SERVICE DEPENDENCY MAPPING <span className="eyebrow-line" />
          </div>
          <h1>
            Service dependencies<span className="title-period">.</span>
          </h1>
          <p>
            Directional service topology, dependency verification, and cascading impact analysis.
          </p>
        </div>
        <div className="updated">
          <button
            className="secondary-button compact"
            onClick={handleTriggerDiscovery}
            disabled={discovering}
          >
            {discovering ? "Discovering..." : "⚡ Verify telemetry"}
          </button>
          <button
            className="primary-button compact"
            onClick={() => setShowAddModal(true)}
          >
            ＋ Add dependency
          </button>
        </div>
      </section>

      {discoveryNote && (
        <div className="notice info-notice" style={{ marginBottom: "16px" }}>
          {discoveryNote}
        </div>
      )}

      {error && (
        <div className="notice error-notice" role="alert" style={{ marginBottom: "16px" }}>
          {error}
        </div>
      )}

      {/* Metrics Row */}
      <section className="metrics-grid">
        <article className="metric-card">
          <div className="metric-top">
            <span>REGISTERED SERVICES</span>
            <span className="metric-icon blue">📦</span>
          </div>
          <div className="metric-value">{services.length}</div>
          <div className="metric-detail">
            <span className="metric-dot blue" />
            {services.filter((s) => s.service_type === "cloud_run").length} Cloud Run services
          </div>
        </article>

        <article className="metric-card">
          <div className="metric-top">
            <span>DEPENDENCY RELATIONSHIPS</span>
            <span className="metric-icon violet">☊</span>
          </div>
          <div className="metric-value">{dependencies.length}</div>
          <div className="metric-detail">
            <span className="metric-dot violet" />
            Directional dependencies mapped
          </div>
        </article>

        <article className="metric-card">
          <div className="metric-top">
            <span>VERIFIED EVIDENCE</span>
            <span className="metric-icon green">✓</span>
          </div>
          <div className="metric-value">{verifiedCount}</div>
          <div className="metric-detail">
            <span className="metric-dot green" />
            {configuredCount} configured architecture links
          </div>
        </article>

        <article className="metric-card">
          <div className="metric-top">
            <span>DOWNSTREAM AT RISK</span>
            <span className="metric-icon amber">⚠️</span>
          </div>
          <div className="metric-value">
            {impactData?.total_impacted ?? 0}
          </div>
          <div className="metric-detail">
            <span className="metric-dot amber" />
            {selectedService ? `If ${selectedService} degrades` : "Select a service to analyze"}
          </div>
        </article>
      </section>

      {/* Main Content: Graph Visualizer + Deep Inspection Drawer */}
      <div className="dependency-layout">
        {/* Graph Panel */}
        <div className="dependency-graph-card">
          <div className="graph-toolbar">
            <div className="graph-controls-left">
              <label htmlFor="service-focus-select" className="graph-select-label">
                Focus service:
              </label>
              <select
                id="service-focus-select"
                className="select-input"
                value={selectedService || ""}
                onChange={(e) => {
                  setSelectedService(e.target.value);
                  setSelectedDependency(null);
                }}
              >
                {services.map((s) => (
                  <option key={s.name} value={s.name}>
                    {s.name} ({s.service_type})
                  </option>
                ))}
              </select>

              <label htmlFor="env-filter-select" className="graph-select-label" style={{ marginLeft: "14px" }}>
                Env:
              </label>
              <select
                id="env-filter-select"
                className="select-input"
                value={envFilter}
                onChange={(e) => setEnvFilter(e.target.value)}
              >
                <option value="all">All environments</option>
                <option value="production">Production</option>
                <option value="staging">Staging</option>
              </select>
            </div>

            <div className="graph-legend">
              <span className="legend-item">
                <span className="legend-dot healthy" /> Healthy
              </span>
              <span className="legend-item">
                <span className="legend-dot impacted" /> Downstream impact
              </span>
              <span className="legend-item">
                <span className="legend-dot selected" /> Selected
              </span>
              <span className="legend-item">
                <span className="legend-line verified" /> Verified
              </span>
              <span className="legend-item">
                <span className="legend-line configured" /> Configured
              </span>
            </div>
          </div>

          {/* Interactive SVG Diagram */}
          <div className="svg-canvas-container">
            {loading ? (
              <div className="timeline-empty">
                <p>Loading service dependency graph...</p>
              </div>
            ) : filteredServices.length === 0 ? (
              <div className="timeline-empty">
                <p>No services registered for this environment.</p>
              </div>
            ) : (
              <svg
                className="dependency-svg"
                viewBox="0 0 900 520"
                preserveAspectRatio="xMidYMid meet"
              >
                <defs>
                  {/* Arrow marker for verified relationships */}
                  <marker
                    id="arrow-verified"
                    viewBox="0 0 10 10"
                    refX="8"
                    refY="5"
                    markerWidth="6"
                    markerHeight="6"
                    orient="auto-start-reverse"
                  >
                    <path d="M 0 1 L 10 5 L 0 9 z" fill="#66d6a4" />
                  </marker>

                  {/* Arrow marker for configured relationships */}
                  <marker
                    id="arrow-configured"
                    viewBox="0 0 10 10"
                    refX="8"
                    refY="5"
                    markerWidth="6"
                    markerHeight="6"
                    orient="auto-start-reverse"
                  >
                    <path d="M 0 1 L 10 5 L 0 9 z" fill="#6e9cff" />
                  </marker>

                  {/* Arrow marker for impacted relationships */}
                  <marker
                    id="arrow-impacted"
                    viewBox="0 0 10 10"
                    refX="8"
                    refY="5"
                    markerWidth="6"
                    markerHeight="6"
                    orient="auto-start-reverse"
                  >
                    <path d="M 0 1 L 10 5 L 0 9 z" fill="#f5bd67" />
                  </marker>
                </defs>

                {/* Render Directional Edges (source -> target) */}
                {filteredDependencies.map((dep) => {
                  const p1 = nodePositions[dep.source];
                  const p2 = nodePositions[dep.target];
                  if (!p1 || !p2) return null;

                  const isSelected =
                    selectedDependency?.id === dep.id ||
                    (selectedDependency?.source === dep.source &&
                      selectedDependency?.target === dep.target);

                  const isConnectingSelected =
                    dep.source === selectedService || dep.target === selectedService;

                  const isImpactedEdge =
                    impactedServiceMap.has(dep.source) && dep.target === selectedService;

                  const markerId = isImpactedEdge
                    ? "url(#arrow-impacted)"
                    : dep.verification_status === "verified"
                    ? "url(#arrow-verified)"
                    : "url(#arrow-configured)";

                  const strokeColor = isImpactedEdge
                    ? "#f5bd67"
                    : isConnectingSelected
                    ? "#6be2dc"
                    : dep.verification_status === "verified"
                    ? "#66d6a4"
                    : "#384766";

                  // Control curve for smooth visual routing
                  const dx = p2.x - p1.x;
                  const dy = p2.y - p1.y;
                  const cx1 = p1.x + dx * 0.45;
                  const cy1 = p1.y;
                  const cx2 = p1.x + dx * 0.55;
                  const cy2 = p2.y;

                  const pathD = `M ${p1.x} ${p1.y} C ${cx1} ${cy1}, ${cx2} ${cy2}, ${p2.x} ${p2.y}`;
                  const midX = (p1.x + p2.x) / 2;
                  const midY = (p1.y + p2.y) / 2 - 8;

                  return (
                    <g
                      key={dep.id || `${dep.source}-${dep.target}-${dep.relationship_type}`}
                      className={`edge-group ${isSelected ? "selected" : ""}`}
                      onClick={() => setSelectedDependency(dep)}
                      style={{ cursor: "pointer" }}
                    >
                      <path
                        d={pathD}
                        fill="none"
                        stroke={strokeColor}
                        strokeWidth={isSelected ? 3.5 : isConnectingSelected ? 2.5 : 1.5}
                        strokeDasharray={dep.verification_status === "configured" ? "5 4" : "none"}
                        markerEnd={markerId}
                        opacity={isConnectingSelected ? 1 : 0.65}
                      />
                      {/* Edge Label Pill */}
                      <rect
                        x={midX - 38}
                        y={midY - 9}
                        width="76"
                        height="18"
                        rx="4"
                        fill="#111725"
                        stroke={strokeColor}
                        strokeWidth="1"
                        opacity="0.9"
                      />
                      <text
                        x={midX}
                        y={midY + 3.5}
                        textAnchor="middle"
                        fill="#c4cfde"
                        fontSize="9"
                        fontFamily="DM Mono, monospace"
                      >
                        {dep.relationship_type.replace(/_/g, " ")}
                      </text>
                    </g>
                  );
                })}

                {/* Render Service Nodes */}
                {filteredServices.map((svc) => {
                  const pos = nodePositions[svc.name];
                  if (!pos) return null;

                  const isSelected = svc.name === selectedService;
                  const impactInfo = impactedServiceMap.get(svc.name);
                  const isImpacted = !!impactInfo;
                  const isUpstream = upstreamDependencies.some(
                    (d) => d.target === svc.name
                  );

                  let nodeStroke = "#263148";
                  let nodeFill = "#151d2d";

                  if (isSelected) {
                    nodeStroke = "#6be2dc";
                    nodeFill = "#19283f";
                  } else if (isImpacted) {
                    nodeStroke = impactInfo.impact_level === "confirmed" ? "#ff7e8b" : "#f5bd67";
                    nodeFill = "#261c28";
                  } else if (isUpstream) {
                    nodeStroke = "#6e9cff";
                    nodeFill = "#142036";
                  }

                  const icon = SERVICE_TYPE_ICONS[svc.service_type] || "📦";

                  return (
                    <g
                      key={svc.name}
                      transform={`translate(${pos.x - 75}, ${pos.y - 32})`}
                      className={`node-group ${isSelected ? "selected-node" : ""} ${
                        isImpacted ? "impacted-node" : ""
                      }`}
                      onClick={() => {
                        setSelectedService(svc.name);
                        setSelectedDependency(null);
                      }}
                      style={{ cursor: "pointer" }}
                    >
                      {/* Background Card */}
                      <rect
                        width="150"
                        height="64"
                        rx="8"
                        fill={nodeFill}
                        stroke={nodeStroke}
                        strokeWidth={isSelected ? 2.5 : isImpacted ? 2 : 1.5}
                        filter="drop-shadow(0 4px 10px rgba(0,0,0,0.35))"
                      />

                      {/* Health Status Dot */}
                      <circle
                        cx="16"
                        cy="20"
                        r="5"
                        fill={
                          svc.health_status === "healthy"
                            ? "#66d6a4"
                            : svc.health_status === "unhealthy"
                            ? "#ff7e8b"
                            : svc.health_status === "degraded"
                            ? "#f5bd67"
                            : "#8d9ab3"
                        }
                      />

                      {/* Icon */}
                      <text x="30" y="24" fontSize="13">
                        {icon}
                      </text>

                      {/* Service Name */}
                      <text
                        x="50"
                        y="23"
                        fill="#e9edf6"
                        fontSize="12"
                        fontWeight="600"
                        fontFamily="DM Sans, sans-serif"
                      >
                        {svc.name.length > 13 ? svc.name.slice(0, 12) + "…" : svc.name}
                      </text>

                      {/* Service Type & Impact Badge */}
                      <text
                        x="16"
                        y="48"
                        fill="#8d9ab3"
                        fontSize="10"
                        fontFamily="DM Mono, monospace"
                      >
                        {svc.service_type}
                      </text>

                      {isImpacted && (
                        <text
                          x="138"
                          y="48"
                          textAnchor="end"
                          fill={impactInfo.impact_level === "confirmed" ? "#ff7e8b" : "#f5bd67"}
                          fontSize="9"
                          fontWeight="700"
                          fontFamily="DM Sans, sans-serif"
                        >
                          {impactInfo.impact_level === "confirmed" ? "CONFIRMED" : "POTENTIAL"}
                        </text>
                      )}
                    </g>
                  );
                })}
              </svg>
            )}
          </div>
        </div>

        {/* Right Inspection & Downstream Impact Panel */}
        <div className="dependency-inspector-panel">
          {selectedService ? (
            <div className="inspector-content">
              <div className="inspector-header">
                <div className="eyebrow">SERVICE DETAILS & IMPACT</div>
                <h2>{selectedService}</h2>
                <div className="badge-row">
                  <span className="service-type-badge">
                    {services.find((s) => s.name === selectedService)?.service_type || "microservice"}
                  </span>
                  <span className="env-badge">
                    {services.find((s) => s.name === selectedService)?.environment || "production"}
                  </span>
                  <span
                    className={`health-badge ${
                      services.find((s) => s.name === selectedService)?.health_status || "unknown"
                    }`}
                  >
                    ● {services.find((s) => s.name === selectedService)?.health_status || "unknown"}
                  </span>
                </div>
              </div>

              {/* Downstream Impact Section */}
              <div className="inspector-section">
                <div className="section-title-row">
                  <h3>Cascading Downstream Impact</h3>
                  <span className="impact-count-pill">
                    {impactData?.total_impacted ?? 0} AFFECTED
                  </span>
                </div>
                <p className="inspector-help">
                  Services that depend directly or transitively on <strong>{selectedService}</strong> and may be impacted by outages.
                </p>

                {impactLoading ? (
                  <p className="muted">Analyzing dependency graph...</p>
                ) : !impactData || impactData.impacted_services.length === 0 ? (
                  <div className="impact-empty-box">
                    <span>✓ No downstream services depend on this service.</span>
                  </div>
                ) : (
                  <div className="impact-cards-list">
                    {impactData.impacted_services.map((item) => (
                      <div
                        key={item.service_name}
                        className={`impact-service-card ${item.impact_level}`}
                      >
                        <div className="impact-card-top">
                          <strong>{item.service_name}</strong>
                          <span className={`impact-level-tag ${item.impact_level}`}>
                            {item.impact_level.toUpperCase()} IMPACT
                          </span>
                        </div>
                        <div className="impact-card-meta">
                          <span>Depth {item.depth} ({item.depth === 1 ? "Direct caller" : "Transitive"})</span>
                          <span>·</span>
                          <span className="impact-path">
                            Path: {item.dependency_path.join(" ──► ")}
                          </span>
                        </div>
                        <ul className="impact-reasons-list">
                          {item.reasons.map((r, i) => (
                            <li key={i}>{r}</li>
                          ))}
                        </ul>
                      </div>
                    ))}
                  </div>
                )}
              </div>

              {/* Upstream Dependencies Section */}
              <div className="inspector-section">
                <h3>Dependencies Required by {selectedService}</h3>
                <p className="inspector-help">
                  Services that <strong>{selectedService}</strong> depends upon to function.
                </p>

                {upstreamDependencies.length === 0 ? (
                  <p className="muted">No upstream dependencies declared.</p>
                ) : (
                  <div className="dependency-list">
                    {upstreamDependencies.map((dep) => (
                      <div
                        key={dep.id || `${dep.source}-${dep.target}`}
                        className="dep-item-card"
                        onClick={() => setSelectedDependency(dep)}
                      >
                        <div className="dep-item-top">
                          <span className="dep-target-name">→ {dep.target}</span>
                          <span className={`status-pill ${dep.verification_status}`}>
                            {dep.verification_status}
                          </span>
                        </div>
                        <div className="dep-item-type">
                          Type: <code>{dep.relationship_type}</code> · Confidence: {dep.confidence}
                        </div>
                        <div className="dep-item-evidence">{dep.evidence}</div>
                      </div>
                    ))}
                  </div>
                )}
              </div>

              {/* Downstream Callers */}
              <div className="inspector-section">
                <h3>Direct Callers (Depends on {selectedService})</h3>
                {downstreamDependencies.length === 0 ? (
                  <p className="muted">No direct callers declared.</p>
                ) : (
                  <div className="dependency-list">
                    {downstreamDependencies.map((dep) => (
                      <div
                        key={dep.id || `${dep.source}-${dep.target}`}
                        className="dep-item-card"
                        onClick={() => setSelectedDependency(dep)}
                      >
                        <div className="dep-item-top">
                          <span className="dep-target-name">← {dep.source}</span>
                          <span className={`status-pill ${dep.verification_status}`}>
                            {dep.verification_status}
                          </span>
                        </div>
                        <div className="dep-item-type">
                          Type: <code>{dep.relationship_type}</code>
                        </div>
                        <div className="dep-item-evidence">{dep.evidence}</div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          ) : (
            <div className="inspector-empty">
              <p>Select a service node in the graph to inspect its dependencies and analyze downstream impact.</p>
            </div>
          )}

          {/* Selected Relationship Deep Inspector */}
          {selectedDependency && (
            <div className="dependency-evidence-modal-box">
              <div className="modal-header-compact">
                <h4>Relationship Evidence</h4>
                <button
                  className="close-button-compact"
                  onClick={() => setSelectedDependency(null)}
                >
                  ✕
                </button>
              </div>
              <div className="evidence-detail-row">
                <span>Direction:</span>
                <strong>
                  {selectedDependency.source} ──► {selectedDependency.target}
                </strong>
              </div>
              <div className="evidence-detail-row">
                <span>Type:</span>
                <code>{selectedDependency.relationship_type}</code>
              </div>
              <div className="evidence-detail-row">
                <span>Status:</span>
                <span className={`status-pill ${selectedDependency.verification_status}`}>
                  {selectedDependency.verification_status}
                </span>
              </div>
              <div className="evidence-detail-row">
                <span>Confidence:</span>
                <span>{selectedDependency.confidence}</span>
              </div>
              <div className="evidence-block-text">
                <strong>Source Evidence:</strong>
                <p>{selectedDependency.evidence}</p>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Add Dependency Modal */}
      {showAddModal && (
        <div className="modal-overlay" onClick={() => setShowAddModal(false)}>
          <div className="modal-window" onClick={(e) => e.stopPropagation()}>
            <div className="modal-top">
              <h3>Configure Service Dependency</h3>
              <button
                className="close-button"
                onClick={() => setShowAddModal(false)}
              >
                ✕
              </button>
            </div>
            <p className="modal-lede">
              Declare an explicit directional dependency relationship between two services.
            </p>

            <form onSubmit={handleAddDependency} className="modal-form">
              <div className="form-group">
                <label>Source Service (Caller / Dependent):</label>
                <input
                  type="text"
                  list="services-list"
                  className="text-input"
                  placeholder="e.g. order-service"
                  value={newDep.source}
                  onChange={(e) =>
                    setNewDep({ ...newDep, source: e.target.value })
                  }
                  required
                />
              </div>

              <div className="form-group">
                <label>Target Service (Callee / Dependency):</label>
                <input
                  type="text"
                  list="services-list"
                  className="text-input"
                  placeholder="e.g. payment-api"
                  value={newDep.target}
                  onChange={(e) =>
                    setNewDep({ ...newDep, target: e.target.value })
                  }
                  required
                />
                <datalist id="services-list">
                  {services.map((s) => (
                    <option key={s.name} value={s.name} />
                  ))}
                </datalist>
              </div>

              <div className="form-row-2">
                <div className="form-group">
                  <label>Relationship Type:</label>
                  <select
                    className="select-input"
                    value={newDep.relationship_type}
                    onChange={(e) =>
                      setNewDep({
                        ...newDep,
                        relationship_type: e.target.value,
                      })
                    }
                  >
                    <option value="sync_http">sync_http (REST / HTTP)</option>
                    <option value="database_connection">database_connection (SQL / Firestore)</option>
                    <option value="cache_lookup">cache_lookup (Redis / Memcached)</option>
                    <option value="pubsub_event">pubsub_event (Pub/Sub Event)</option>
                    <option value="auth_token_validation">auth_token_validation (JWT / OAuth)</option>
                    <option value="grpc">grpc (gRPC RPC)</option>
                  </select>
                </div>

                <div className="form-group">
                  <label>Confidence:</label>
                  <select
                    className="select-input"
                    value={newDep.confidence}
                    onChange={(e) =>
                      setNewDep({ ...newDep, confidence: e.target.value })
                    }
                  >
                    <option value="high">High</option>
                    <option value="medium">Medium</option>
                    <option value="low">Low</option>
                  </select>
                </div>
              </div>

              <div className="form-group">
                <label>Evidence or Configuration Source:</label>
                <textarea
                  className="text-input textarea"
                  rows="3"
                  placeholder="e.g. Verified from container environment variable DATABASE_URL or Architecture runbook doc_01"
                  value={newDep.evidence}
                  onChange={(e) =>
                    setNewDep({ ...newDep, evidence: e.target.value })
                  }
                  required
                />
              </div>

              <div className="modal-actions">
                <button
                  type="button"
                  className="secondary-button"
                  onClick={() => setShowAddModal(false)}
                >
                  Cancel
                </button>
                <button type="submit" className="primary-button">
                  Save Dependency
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
