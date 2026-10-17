import React, {
  useEffect,
  useState,
  useMemo,
  useCallback,
  useRef,
  memo,
} from "react";
import { request, api } from "../firebase.js";

// -------------------------------------------------------------------
// Service Icons and Labels
// -------------------------------------------------------------------

const SERVICE_TYPE_ICONS = {
  cloud_run: "☁️",
  microservice: "📦",
  database: "🗄️",
  cache: "⚡",
  event_broker: "📨",
  external_api: "🌐",
  gateway: "🚪",
  storage: "💾",
  other: "⚙️",
};

const SERVICE_TYPE_LABELS = {
  cloud_run: "Cloud Run",
  microservice: "Microservice",
  database: "Database",
  cache: "Cache",
  event_broker: "Event Broker",
  external_api: "External API",
  gateway: "API Gateway",
  storage: "Storage",
  other: "Service",
};

// Layout geometry constants
const NODE_WIDTH = 180;
const NODE_HEIGHT = 56;
const COL_GAP = 140;
const ROW_GAP = 28;
const ROW_STRIDE = NODE_HEIGHT + ROW_GAP; // 84px

function inferServiceType(name) {
  const lower = (name || "").toLowerCase();
  if (lower.includes("db") || lower.includes("sql") || lower.includes("postgres") || lower.includes("firestore")) {
    return "database";
  }
  if (lower.includes("cache") || lower.includes("redis") || lower.includes("memcache")) {
    return "cache";
  }
  if (lower.includes("gateway") || lower.includes("ingress") || lower.includes("proxy")) {
    return "gateway";
  }
  if (lower.includes("pubsub") || lower.includes("kafka") || lower.includes("queue") || lower.includes("broker")) {
    return "event_broker";
  }
  if (lower.includes("storage") || lower.includes("bucket") || lower.includes("gcs") || lower.includes("s3")) {
    return "storage";
  }
  if (lower.includes("ext") || lower.includes("thirdparty") || lower.includes("stripe")) {
    return "external_api";
  }
  return "microservice";
}

// -------------------------------------------------------------------
// Memoized Service Node Component
// -------------------------------------------------------------------

const DependencyGraphNode = memo(function DependencyGraphNode({
  node,
  pos,
  isSelected,
  isImpacted,
  impactLevel,
  isFocusRoot,
  isCaller,
  isDependency,
  isDimmed,
  onSelect,
  onHover,
}) {
  const icon = SERVICE_TYPE_ICONS[node.service_type] || "📦";
  const w = NODE_WIDTH;
  const h = NODE_HEIGHT;
  const rx = 8;

  // Background and border styling based on active state
  let bgFill = "var(--graph-node-bg)";
  let strokeColor = "var(--graph-node-stroke)";
  let strokeWidth = 1.2;

  if (isSelected) {
    bgFill = "var(--graph-node-bg-selected)";
    strokeColor = "var(--brand-primary)";
    strokeWidth = 2.2;
  } else if (isImpacted) {
    bgFill = impactLevel === "confirmed" ? "var(--status-error-subtle)" : "var(--status-warning-subtle)";
    strokeColor = impactLevel === "confirmed" ? "var(--status-error)" : "var(--status-warning)";
    strokeWidth = 2;
  } else if (isFocusRoot) {
    bgFill = "var(--brand-subtle)";
    strokeColor = "var(--brand-primary)";
    strokeWidth = 2;
  } else if (isCaller) {
    bgFill = "var(--status-info-subtle)";
    strokeColor = "var(--status-info)";
    strokeWidth = 1.6;
  } else if (isDependency) {
    bgFill = "var(--status-success-subtle)";
    strokeColor = "var(--status-success)";
    strokeWidth = 1.6;
  }

  // Health dot color
  const healthColor =
    node.health_status === "healthy"
      ? "var(--status-success)"
      : node.health_status === "unhealthy"
      ? "var(--status-error)"
      : node.health_status === "degraded"
      ? "var(--status-warning)"
      : "var(--text-muted)";

  // Display label truncation
  const displayName = node.name.length > 18 ? `${node.name.slice(0, 17)}…` : node.name;

  return (
    <g
      className={`dep-node-group ${isSelected ? "selected" : ""} ${
        isDimmed ? "dimmed" : ""
      }`}
      transform={`translate(${pos.x - w / 2}, ${pos.y - h / 2})`}
      onClick={() => onSelect(node.name)}
      onMouseEnter={() => onHover(node.name)}
      onMouseLeave={() => onHover(null)}
      style={{
        cursor: "pointer",
        opacity: isDimmed ? 0.35 : 1,
        transition: "opacity 0.2s ease, transform 0.2s ease",
      }}
    >
      <title>{`${node.name} (${SERVICE_TYPE_LABELS[node.service_type] || node.service_type}) - Environment: ${node.environment || "production"} - Health: ${node.health_status || "unknown"}`}</title>

      {/* Selected Accent Outer Highlight */}
      {isSelected && (
        <rect
          x="-3"
          y="-3"
          width={w + 6}
          height={h + 6}
          rx={rx + 2}
          fill="none"
          stroke="var(--brand-primary)"
          strokeWidth="1.2"
          strokeOpacity="0.5"
        />
      )}

      {/* Node Card Background */}
      <rect
        width={w}
        height={h}
        rx={rx}
        fill={bgFill}
        stroke={strokeColor}
        strokeWidth={strokeWidth}
      />

      {/* Health Status Indicator Dot */}
      <circle cx="16" cy="18" r="4.5" fill={healthColor} />

      {/* Service Icon */}
      <text x="28" y="22" fontSize="13">
        {icon}
      </text>

      {/* Service Name */}
      <text
        x="48"
        y="21"
        fill="var(--graph-node-text)"
        fontSize="12"
        fontWeight="600"
        fontFamily="Inter, sans-serif"
      >
        {displayName}
      </text>

      {/* Service Type Tag (Bottom Left) */}
      <text
        x="14"
        y="42"
        fill="var(--graph-node-subtext)"
        fontSize="10"
        fontWeight="500"
        fontFamily="JetBrains Mono, monospace"
      >
        {node.service_type}
      </text>

      {/* Status Pill on Bottom Right */}
      {isFocusRoot ? (
        <g transform={`translate(${w - 56}, 32)`}>
          <rect width="46" height="15" rx="3" fill="var(--brand-subtle)" stroke="var(--brand-primary)" strokeWidth="1" />
          <text x="23" y="11" textAnchor="middle" fill="var(--brand-text)" fontSize="8.5" fontWeight="700" fontFamily="JetBrains Mono">
            FOCUS
          </text>
        </g>
      ) : isImpacted ? (
        <g transform={`translate(${w - 68}, 32)`}>
          <rect
            width="58"
            height="15"
            rx="3"
            fill={impactLevel === "confirmed" ? "var(--status-error-subtle)" : "var(--status-warning-subtle)"}
            stroke={impactLevel === "confirmed" ? "var(--status-error)" : "var(--status-warning)"}
            strokeWidth="1"
          />
          <text
            x="29"
            y="11"
            textAnchor="middle"
            fill={impactLevel === "confirmed" ? "var(--status-error-text)" : "var(--status-warning-text)"}
            fontSize="8.5"
            fontWeight="700"
            fontFamily="JetBrains Mono"
          >
            {impactLevel === "confirmed" ? "AT RISK" : "IMPACT"}
          </text>
        </g>
      ) : (
        <text
          x={w - 12}
          y="42"
          textAnchor="end"
          fill="var(--text-muted)"
          fontSize="9.5"
          fontFamily="JetBrains Mono, monospace"
        >
          {node.environment || "prod"}
        </text>
      )}
    </g>
  );
});

// -------------------------------------------------------------------
// Memoized Directional Curved Edge Component
// -------------------------------------------------------------------

const DependencyGraphEdge = memo(function DependencyGraphEdge({
  edge,
  sourcePos,
  targetPos,
  isSelected,
  isConnectedToSelected,
  isImpacted,
  isDimmed,
  showLabelAlways,
  isHovered,
  onSelect,
  onHover,
}) {
  if (!sourcePos || !targetPos) return null;

  // Calculate perimeter connection points based on relative orientation
  const isLeftToRight = targetPos.x > sourcePos.x;
  const isSameColumn = Math.abs(targetPos.x - sourcePos.x) < 20;

  let sx, sy, tx, ty, pathD, midX, midY;

  if (isSameColumn) {
    // Edge within the same column: loop out to the right
    sx = sourcePos.x + NODE_WIDTH / 2;
    sy = sourcePos.y;
    tx = targetPos.x + NODE_WIDTH / 2;
    ty = targetPos.y;
    const offset = 48 + Math.min(60, Math.abs(ty - sy) * 0.15);
    pathD = `M ${sx} ${sy} C ${sx + offset} ${sy}, ${tx + offset} ${ty}, ${tx} ${ty}`;
    midX = sx + offset;
    midY = (sy + ty) / 2;
  } else if (isLeftToRight) {
    // Normal forward flow: Source right edge -> Target left edge
    sx = sourcePos.x + NODE_WIDTH / 2;
    sy = sourcePos.y;
    tx = targetPos.x - NODE_WIDTH / 2;
    ty = targetPos.y;
    const dx = tx - sx;
    const cx1 = sx + dx * 0.45;
    const cy1 = sy;
    const cx2 = tx - dx * 0.45;
    const cy2 = ty;
    pathD = `M ${sx} ${sy} C ${cx1} ${cy1}, ${cx2} ${cy2}, ${tx} ${ty}`;
    midX = (sx + tx) / 2;
    midY = (sy + ty) / 2;
  } else {
    // Backward flow: Loop through bottom or top
    sx = sourcePos.x;
    sy = sourcePos.y + NODE_HEIGHT / 2;
    tx = targetPos.x;
    ty = targetPos.y + NODE_HEIGHT / 2;
    const dy = 44;
    pathD = `M ${sx} ${sy} C ${sx} ${sy + dy}, ${tx} ${ty + dy}, ${tx} ${ty}`;
    midX = (sx + tx) / 2;
    midY = Math.max(sy, ty) + dy / 2;
  }

  // Edge stroke and marker selection
  let strokeColor = "var(--border-strong)";
  let strokeWidth = 1.4;
  let strokeDash = edge.verification_status === "configured" ? "6 4" : "none";
  let markerId = "url(#arrow-configured)";

  if (isSelected) {
    strokeColor = "var(--brand-primary)";
    strokeWidth = 2.6;
    strokeDash = "none";
    markerId = "url(#arrow-selected)";
  } else if (isImpacted) {
    strokeColor = "var(--status-warning)";
    strokeWidth = 2.0;
    strokeDash = "none";
    markerId = "url(#arrow-impacted)";
  } else if (isConnectedToSelected) {
    strokeColor = edge.verification_status === "verified" ? "var(--graph-edge-verified)" : "var(--brand-primary)";
    strokeWidth = 1.8;
    markerId = edge.verification_status === "verified" ? "url(#arrow-verified)" : "url(#arrow-configured)";
  } else if (edge.verification_status === "verified") {
    strokeColor = "#4b9e7b";
    strokeWidth = 1.5;
    markerId = "url(#arrow-verified)";
  }

  const shouldShowLabel = showLabelAlways || isHovered || isSelected;
  const relLabel = (edge.relationship_type || "sync_http").replace(/_/g, " ");

  return (
    <g
      className={`dep-edge-group ${isSelected ? "selected" : ""} ${
        isDimmed ? "dimmed" : ""
      }`}
      onClick={() => onSelect(edge)}
      onMouseEnter={() => onHover(edge)}
      onMouseLeave={() => onHover(null)}
      style={{
        cursor: "pointer",
        opacity: isDimmed ? 0.2 : 1,
        transition: "opacity 0.2s ease",
      }}
    >
      {/* Invisible wider hit path for effortless hover and selection */}
      <path
        d={pathD}
        fill="none"
        stroke="transparent"
        strokeWidth="16"
        style={{ cursor: "pointer" }}
      />

      {/* Visible directional path */}
      <path
        d={pathD}
        fill="none"
        stroke={strokeColor}
        strokeWidth={strokeWidth}
        strokeDasharray={strokeDash}
        markerEnd={markerId}
      />

      {/* Floating relationship pill (shown on hover, selection, or when toggle enabled) */}
      {shouldShowLabel && (
        <g
          transform={`translate(${midX}, ${midY - 9})`}
          style={{ pointerEvents: "none" }}
        >
          <rect
            x={-Math.max(38, relLabel.length * 3.4 + 10)}
            y="-8"
            width={Math.max(76, relLabel.length * 6.8 + 20)}
            height="18"
            rx="4"
            fill="var(--bg-surface-elevated)"
            stroke={strokeColor}
            strokeWidth="1"
          />
          <text
            x="0"
            y="4.5"
            textAnchor="middle"
            fill="var(--text-primary)"
            fontSize="9"
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

// -------------------------------------------------------------------
// Main Service Dependency View
// -------------------------------------------------------------------

export default function ServiceDependencyView({
  initialSelectedService = null,
  onOpenIncident = null,
  onBack = null,
}) {
  const [services, setServices] = useState([]);
  const [dependencies, setDependencies] = useState([]);
  const [selectedService, setSelectedService] = useState(initialSelectedService);
  const [selectedDependency, setSelectedDependency] = useState(null);
  const [hoveredDependency, setHoveredDependency] = useState(null);
  const [hoveredService, setHoveredService] = useState(null);

  const [impactData, setImpactData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [impactLoading, setImpactLoading] = useState(false);
  const [error, setError] = useState("");

  // Filter & Focus Controls
  const [envFilter, setEnvFilter] = useState("all");
  const [focusMode, setFocusMode] = useState("focus"); // 'focus' (1-hop) | 'extended' (2-hop) | 'all'
  const [showLabels, setShowLabels] = useState(false);

  // Large graph handling: batching & search
  const [allModeLimit, setAllModeLimit] = useState(50);
  const [serviceSearch, setServiceSearch] = useState("");

  // Stable callbacks so React.memo on DependencyGraphNode and DependencyGraphEdge is effective
  const handleSelectService = useCallback((serviceName) => {
    setSelectedService(serviceName);
    setSelectedDependency(null);
  }, []);

  const handleHoverService = useCallback((serviceName) => {
    setHoveredService(serviceName);
  }, []);

  const handleSelectEdge = useCallback((edge) => {
    setSelectedDependency(edge);
  }, []);

  const handleHoverEdge = useCallback((edge) => {
    setHoveredDependency(edge);
  }, []);

  // Pan & Zoom Viewport Controls
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const [isDragging, setIsDragging] = useState(false);
  const [dragStart, setDragStart] = useState({ x: 0, y: 0 });
  const [containerSize, setContainerSize] = useState({ width: 900, height: 580 });
  const svgWrapperRef = useRef(null);
  const lastFitKeyRef = useRef("");

  // Measure visible canvas container size via ResizeObserver
  useEffect(() => {
    const el = svgWrapperRef.current;
    if (!el) return;
    const updateSize = () => {
      const rect = el.getBoundingClientRect();
      if (rect.width > 0 && rect.height > 0) {
        setContainerSize({
          width: Math.round(rect.width),
          height: Math.round(rect.height),
        });
      }
    };
    updateSize();
    const observer = new ResizeObserver(updateSize);
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  // Modal and Discovery State
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

  // Load baseline services and dependency relationships
  const loadData = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const data = await request("/dependencies");
      const loadedServices = Array.isArray(data.services) ? data.services : [];
      const loadedDeps = Array.isArray(data.dependencies) ? data.dependencies : [];
      setServices(loadedServices);
      setDependencies(loadedDeps);

      if (!selectedService && loadedServices.length > 0) {
        setSelectedService(initialSelectedService || loadedServices[0].name);
      }
    } catch (err) {
      setError(err.message || "Failed to load service dependencies.");
    } finally {
      setLoading(false);
    }
  }, [initialSelectedService, selectedService]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  // Synchronize selectedService when initialSelectedService changes
  useEffect(() => {
    if (initialSelectedService) {
      setSelectedService(initialSelectedService);
      setSelectedDependency(null);
      setFocusMode("focus");
    }
  }, [initialSelectedService]);

  // When selected service changes, fetch downstream cascading impact
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

  // 1. Strict Node Deduplication & Synthesis
  const deduplicatedServices = useMemo(() => {
    const map = new Map();

    // 1. First add explicitly registered services
    for (const svc of services) {
      if (!svc || !svc.name) continue;
      if (envFilter !== "all" && svc.environment && svc.environment !== envFilter) continue;
      if (!map.has(svc.name)) {
        map.set(svc.name, {
          ...svc,
          environment: svc.environment || "production",
          health_status: svc.health_status || "unknown",
        });
      }
    }

    // 2. Ensure services appearing in dependencies also exist as nodes
    for (const dep of dependencies) {
      if (envFilter !== "all" && dep.environment && dep.environment !== envFilter) continue;
      if (dep.source && !map.has(dep.source)) {
        map.set(dep.source, {
          name: dep.source,
          service_type: inferServiceType(dep.source),
          environment: dep.environment || "production",
          health_status: "unknown",
        });
      }
      if (dep.target && !map.has(dep.target)) {
        map.set(dep.target, {
          name: dep.target,
          service_type: inferServiceType(dep.target),
          environment: dep.environment || "production",
          health_status: "unknown",
        });
      }
    }

    return Array.from(map.values());
  }, [services, dependencies, envFilter]);

  // 2. Edge Deduplication & Relationship Grouping
  const deduplicatedDependencies = useMemo(() => {
    const map = new Map();

    for (const dep of dependencies) {
      if (!dep || !dep.source || !dep.target) continue;
      if (envFilter !== "all" && dep.environment && dep.environment !== envFilter) continue;

      const edgeKey = `${dep.source}->${dep.target}`;
      if (!map.has(edgeKey)) {
        map.set(edgeKey, {
          ...dep,
          id: dep.id || edgeKey,
          all_types: [dep.relationship_type],
          raw_dependencies: [dep],
        });
      } else {
        const existing = map.get(edgeKey);
        if (!existing.all_types.includes(dep.relationship_type)) {
          existing.all_types.push(dep.relationship_type);
        }
        existing.raw_dependencies.push(dep);
        // Elevate status if any relationship is verified
        if (dep.verification_status === "verified") {
          existing.verification_status = "verified";
        }
      }
    }

    return Array.from(map.values());
  }, [dependencies, envFilter]);

  // Fast service lookup
  const serviceMap = useMemo(() => {
    const map = new Map();
    for (const s of deduplicatedServices) {
      map.set(s.name, s);
    }
    return map;
  }, [deduplicatedServices]);

  // 3. Focus Neighborhood & All Services Batching Calculation
  const { visibleServices, visibleDependencies, hiddenServiceCount, totalMatchingInAllMode } = useMemo(() => {
    if (focusMode === "all") {
      let matching = deduplicatedServices;
      const q = serviceSearch.trim().toLowerCase();
      if (q) {
        matching = matching.filter(
          (s) =>
            s.name.toLowerCase().includes(q) ||
            (s.service_type && s.service_type.toLowerCase().includes(q)) ||
            (s.environment && s.environment.toLowerCase().includes(q))
        );
      }

      // Ensure currently selected service is always included in visible subset if present in catalog
      const selectedObj = selectedService ? deduplicatedServices.find((s) => s.name === selectedService) : null;
      let sliced = matching.slice(0, allModeLimit);
      if (selectedObj && !sliced.some((s) => s.name === selectedService)) {
        sliced = [selectedObj, ...sliced.slice(0, Math.max(0, allModeLimit - 1))];
      }

      const visibleSet = new Set(sliced.map((s) => s.name));
      // Only render edges where both endpoints are visible in the subset
      const filteredEdges = deduplicatedDependencies.filter(
        (d) => visibleSet.has(d.source) && visibleSet.has(d.target)
      );

      return {
        visibleServices: sliced,
        visibleDependencies: filteredEdges,
        hiddenServiceCount: Math.max(0, matching.length - sliced.length),
        totalMatchingInAllMode: matching.length,
      };
    }

    if (!selectedService) {
      const sliced = deduplicatedServices.slice(0, allModeLimit);
      return {
        visibleServices: sliced,
        visibleDependencies: [],
        hiddenServiceCount: Math.max(0, deduplicatedServices.length - sliced.length),
        totalMatchingInAllMode: deduplicatedServices.length,
      };
    }

    const focusSet = new Set();
    focusSet.add(selectedService);

    // 1-hop: immediate callers and direct dependencies
    const hop1Callers = new Set();
    const hop1Deps = new Set();

    for (const dep of deduplicatedDependencies) {
      if (dep.target === selectedService) {
        hop1Callers.add(dep.source);
        focusSet.add(dep.source);
      }
      if (dep.source === selectedService) {
        hop1Deps.add(dep.target);
        focusSet.add(dep.target);
      }
    }

    // 2-hop (if in extended focus mode)
    if (focusMode === "extended") {
      for (const dep of deduplicatedDependencies) {
        if (hop1Callers.has(dep.target) || hop1Deps.has(dep.source)) {
          focusSet.add(dep.source);
          focusSet.add(dep.target);
        }
      }
    }

    const filteredNodes = deduplicatedServices.filter((s) => focusSet.has(s.name));
    const filteredEdges = deduplicatedDependencies.filter(
      (d) => focusSet.has(d.source) && focusSet.has(d.target)
    );

    return {
      visibleServices: filteredNodes,
      visibleDependencies: filteredEdges,
      hiddenServiceCount: Math.max(0, deduplicatedServices.length - filteredNodes.length),
      totalMatchingInAllMode: deduplicatedServices.length,
    };
  }, [
    selectedService,
    focusMode,
    deduplicatedServices,
    deduplicatedDependencies,
    serviceSearch,
    allModeLimit,
  ]);

  // Impacted services map for fast lookup
  const impactedServiceMap = useMemo(() => {
    const map = new Map();
    if (impactData?.impacted_services) {
      for (const item of impactData.impacted_services) {
        map.set(item.service_name, item);
      }
    }
    return map;
  }, [impactData]);

  // Direct upstream dependencies of the selected service
  const selectedUpstreamDeps = useMemo(() => {
    if (!selectedService) return [];
    return deduplicatedDependencies.filter((d) => d.source === selectedService);
  }, [deduplicatedDependencies, selectedService]);

  // Direct downstream callers of the selected service
  const selectedDownstreamCallers = useMemo(() => {
    if (!selectedService) return [];
    return deduplicatedDependencies.filter((d) => d.target === selectedService);
  }, [deduplicatedDependencies, selectedService]);

  // Fast O(1) Sets for caller & dependency status checks in node rendering
  const callerSet = useMemo(
    () => new Set(selectedDownstreamCallers.map((d) => d.source)),
    [selectedDownstreamCallers]
  );
  const depSet = useMemo(
    () => new Set(selectedUpstreamDeps.map((d) => d.target)),
    [selectedUpstreamDeps]
  );

  // 4. Non-Overlapping Enterprise Graph Layout Algorithm
  const { nodePositions, svgWidth, svgHeight } = useMemo(() => {
    const pos = {};
    const totalVisible = visibleServices.length;
    if (totalVisible === 0) {
      return { nodePositions: pos, svgWidth: 860, svgHeight: 500 };
    }

    let columns = [];

    if (focusMode === "focus" && selectedService) {
      // Clean 3-Column Focus Architecture:
      // Column 0: Direct Callers (depend on selected service)
      // Column 1: Selected Service (prominently centered root)
      // Column 2: Direct Dependencies (called by selected service)
      const col0 = []; // Callers
      const col1 = []; // Root
      const col2 = []; // Dependencies

      for (const svc of visibleServices) {
        if (svc.name === selectedService) {
          col1.push(svc);
        } else if (selectedDownstreamCallers.some((d) => d.source === svc.name)) {
          col0.push(svc);
        } else {
          col2.push(svc);
        }
      }

      columns = [
        { key: "callers", label: "CALLERS (UPSTREAM)", nodes: col0 },
        { key: "focus", label: "SELECTED FOCUS", nodes: col1 },
        { key: "deps", label: "DEPENDENCIES (DOWNSTREAM)", nodes: col2 },
      ];
    } else if (focusMode === "extended" && selectedService) {
      // 5-Column Extended Topology
      const col0 = []; // 2nd-degree Callers
      const col1 = []; // Direct Callers
      const col2 = []; // Selected Service
      const col3 = []; // Direct Dependencies
      const col4 = []; // 2nd-degree Dependencies

      const directCallerNames = new Set(selectedDownstreamCallers.map((d) => d.source));
      const directDepNames = new Set(selectedUpstreamDeps.map((d) => d.target));

      for (const svc of visibleServices) {
        if (svc.name === selectedService) {
          col2.push(svc);
        } else if (directCallerNames.has(svc.name)) {
          col1.push(svc);
        } else if (directDepNames.has(svc.name)) {
          col3.push(svc);
        } else {
          const isUpstream = visibleDependencies.some(
            (d) => d.source === svc.name && directCallerNames.has(d.target)
          );
          if (isUpstream) col0.push(svc);
          else col4.push(svc);
        }
      }

      columns = [
        { key: "callers2", label: "EXTENDED CALLERS", nodes: col0 },
        { key: "callers1", label: "DIRECT CALLERS", nodes: col1 },
        { key: "focus", label: "SELECTED FOCUS", nodes: col2 },
        { key: "deps1", label: "DIRECT DEPENDENCIES", nodes: col3 },
        { key: "deps2", label: "EXTENDED DEPENDENCIES", nodes: col4 },
      ].filter((c) => c.nodes.length > 0);
    } else {
      // "All Services" Full Architecture Layout:
      // Ingress / Gateways -> Core Microservices -> Supporting / Auth -> Datastores
      const gateways = [];
      const coreServices = [];
      const datastores = [];

      for (const svc of visibleServices) {
        const type = svc.service_type;
        if (type === "database" || type === "cache" || type === "storage" || type === "external_api") {
          datastores.push(svc);
        } else if (type === "gateway" || svc.name.includes("gateway") || svc.name.includes("order") || svc.name.includes("frontend")) {
          gateways.push(svc);
        } else {
          coreServices.push(svc);
        }
      }

      // Partition into balanced columns to maintain readable widescreen aspect ratio
      const targetRows = Math.max(5, Math.ceil(Math.sqrt(totalVisible * 1.85)));
      const maxPerCol = Math.min(20, Math.max(6, targetRows));

      const partitionNodes = (items, prefix, labelPrefix) => {
        const batches = [];
        for (let i = 0; i < items.length; i += maxPerCol) {
          batches.push(items.slice(i, i + maxPerCol));
        }
        return batches.map((batch, idx) => ({
          key: `${prefix}_${idx}`,
          label: batches.length > 1 ? `${labelPrefix} (${idx + 1})` : labelPrefix,
          nodes: batch,
        }));
      };

      columns.push(...partitionNodes(gateways, "ingress", "GATEWAYS / INGRESS"));
      columns.push(...partitionNodes(coreServices, "core", "CORE SERVICES"));
      columns.push(...partitionNodes(datastores, "datastores", "PERSISTENCE & DATA"));

      columns = columns.filter((c) => c.nodes.length > 0);
    }

    const numCols = Math.max(columns.length, 1);
    const maxRows = Math.max(1, ...columns.map((c) => c.nodes.length));

    // Dynamic bounded SVG canvas dimensions in graph space
    const computedWidth = Math.max(880, numCols * (NODE_WIDTH + COL_GAP) + 60);
    const computedHeight = Math.max(480, maxRows * ROW_STRIDE + 120);

    const padLeft = (computedWidth - (numCols - 1) * (NODE_WIDTH + COL_GAP) - NODE_WIDTH) / 2 + NODE_WIDTH / 2;

    columns.forEach((col, colIdx) => {
      const colX = numCols === 1 ? computedWidth / 2 : padLeft + colIdx * (NODE_WIDTH + COL_GAP);
      const count = col.nodes.length;
      const totalColHeight = count * ROW_STRIDE - ROW_GAP;
      const startY = 70 + (computedHeight - 110 - totalColHeight) / 2 + NODE_HEIGHT / 2;

      col.nodes.forEach((node, rowIdx) => {
        const colY = startY + rowIdx * ROW_STRIDE;
        pos[node.name] = { x: colX, y: colY };
      });
    });

    return {
      nodePositions: pos,
      svgWidth: computedWidth,
      svgHeight: computedHeight,
    };
  }, [
    visibleServices,
    selectedService,
    focusMode,
    selectedDownstreamCallers,
    selectedUpstreamDeps,
  ]);

  // Bounding box computed from actual rendered node positions (with padding for node dimensions and edge curves)
  const graphBounds = useMemo(() => {
    const nodeNames = Object.keys(nodePositions);
    if (nodeNames.length === 0) {
      return {
        minX: 0,
        maxX: 880,
        minY: 0,
        maxY: 500,
        width: 880,
        height: 500,
        centerX: 440,
        centerY: 250,
      };
    }
    let minX = Infinity;
    let maxX = -Infinity;
    let minY = Infinity;
    let maxY = -Infinity;

    for (const name of nodeNames) {
      const p = nodePositions[name];
      if (!p) continue;
      const left = p.x - NODE_WIDTH / 2;
      const right = p.x + NODE_WIDTH / 2;
      const top = p.y - NODE_HEIGHT / 2;
      const bottom = p.y + NODE_HEIGHT / 2;

      if (left < minX) minX = left;
      if (right > maxX) maxX = right;
      if (top < minY) minY = top;
      if (bottom > maxY) maxY = bottom;
    }

    // Include padding for edge endpoints, markers, and curved bezier paths
    const padding = 32;
    minX -= padding;
    maxX += padding;
    minY -= padding;
    maxY += padding;

    const width = Math.max(1, maxX - minX);
    const height = Math.max(1, maxY - minY);
    const centerX = (minX + maxX) / 2;
    const centerY = (minY + maxY) / 2;

    return { minX, maxX, minY, maxY, width, height, centerX, centerY };
  }, [nodePositions]);

  // Fit rendered graph cleanly into visible container viewport
  const fitToView = useCallback(
    (bounds = graphBounds, currentSize = containerSize) => {
      if (!bounds || bounds.width <= 0 || bounds.height <= 0) return;
      const paddingX = 48;
      const paddingY = 40;
      const availWidth = Math.max(100, currentSize.width - paddingX * 2);
      const availHeight = Math.max(100, currentSize.height - paddingY * 2);

      const scaleX = availWidth / bounds.width;
      const scaleY = availHeight / bounds.height;
      const rawScale = Math.min(scaleX, scaleY);

      // Keep initial zoom sensible: max 1.0 (do not over-magnify small graphs), min 0.18
      const targetZoom = +(Math.min(1.0, Math.max(0.18, rawScale))).toFixed(3);
      const targetPanX = Math.round(currentSize.width / 2 - bounds.centerX * targetZoom);
      const targetPanY = Math.round(currentSize.height / 2 - bounds.centerY * targetZoom);

      setZoom(targetZoom);
      setPan({ x: targetPanX, y: targetPanY });
    },
    [graphBounds, containerSize]
  );

  // Auto-fit when switching modes, loading new services, or when container dimensions change
  const currentFitKey = `${focusMode}-${focusMode === "all" ? visibleServices.length : selectedService}-${containerSize.width}x${containerSize.height}`;

  useEffect(() => {
    if (visibleServices.length === 0) return;
    if (lastFitKeyRef.current !== currentFitKey) {
      lastFitKeyRef.current = currentFitKey;
      fitToView();
    }
  }, [currentFitKey, fitToView, visibleServices.length]);

  // Viewport zoom & pan handlers
  const handleZoomIn = () => setZoom((z) => Math.min(2.5, +(z + 0.15).toFixed(2)));
  const handleZoomOut = () => setZoom((z) => Math.max(0.18, +(z - 0.15).toFixed(2)));
  const handleResetToFocus = () => {
    setFocusMode("focus");
  };

  // Canvas pan dragging with window-level listeners for smooth uninterrupted movement
  const handleMouseDown = useCallback((e) => {
    const target = e.target;
    const isBg =
      target.tagName === "svg" ||
      target.classList?.contains("canvas-bg") ||
      target.classList?.contains("svg-canvas-container");

    if (isBg) {
      setIsDragging(true);
      setDragStart({ x: e.clientX - pan.x, y: e.clientY - pan.y });
    }
  }, [pan.x, pan.y]);

  const handleMouseMove = useCallback((e) => {
    if (!isDragging) return;
    setPan({ x: Math.round(e.clientX - dragStart.x), y: Math.round(e.clientY - dragStart.y) });
  }, [isDragging, dragStart.x, dragStart.y]);

  const handleMouseUp = useCallback(() => {
    setIsDragging(false);
  }, []);

  useEffect(() => {
    if (!isDragging) return;
    window.addEventListener("mousemove", handleMouseMove);
    window.addEventListener("mouseup", handleMouseUp);
    return () => {
      window.removeEventListener("mousemove", handleMouseMove);
      window.removeEventListener("mouseup", handleMouseUp);
    };
  }, [isDragging, handleMouseMove, handleMouseUp]);

  // Wheel zoom centered at cursor position
  useEffect(() => {
    const el = svgWrapperRef.current;
    if (!el) return;

    const handleWheel = (e) => {
      e.preventDefault();
      const zoomFactor = e.deltaY < 0 ? 1.12 : 0.89;
      setZoom((prevZoom) => {
        const nextZoom = +(Math.min(2.5, Math.max(0.18, prevZoom * zoomFactor))).toFixed(3);
        const rect = el.getBoundingClientRect();
        const mouseX = e.clientX - rect.left;
        const mouseY = e.clientY - rect.top;
        setPan((prevPan) => ({
          x: Math.round(mouseX - (mouseX - prevPan.x) * (nextZoom / prevZoom)),
          y: Math.round(mouseY - (mouseY - prevPan.y) * (nextZoom / prevZoom)),
        }));
        return nextZoom;
      });
    };

    el.addEventListener("wheel", handleWheel, { passive: false });
    return () => el.removeEventListener("wheel", handleWheel);
  }, []);

  // Discovery Action
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

  // Add Dependency Action
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

  // Verification counts
  const verifiedCount = useMemo(
    () => dependencies.filter((d) => d.verification_status === "verified").length,
    [dependencies]
  );
  const configuredCount = useMemo(
    () => dependencies.filter((d) => d.verification_status === "configured").length,
    [dependencies]
  );

  const selectedServiceObj = selectedService ? serviceMap.get(selectedService) : null;

  return (
    <div className="dependency-view">
      {/* Back to Overview Navigation */}
      <div className="back-navigation-row">
        <button
          type="button"
          className="back-link"
          onClick={() => (onBack ? onBack() : null)}
          aria-label="Back to Overview"
        >
          ← Back to Overview
        </button>
      </div>

      {/* 1. Header Row */}
      <section className="welcome-row">
        <div>
          <div className="eyebrow">
            SERVICE TOPOLOGY MAP <span className="eyebrow-line" />
          </div>
          <h1>
            Service dependencies<span className="title-period">.</span>
          </h1>
          <p>
            Directional service topology, verified architectural relationships, and cascading impact analysis.
          </p>
        </div>
        <div className="service-map-header-actions">
          <button
            type="button"
            className="secondary-button compact"
            onClick={handleTriggerDiscovery}
            disabled={discovering}
            title="Scan active logs and traces to verify configured relationships"
          >
            {discovering ? "Verifying..." : "⚡ Verify telemetry"}
          </button>
          <button
            type="button"
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

      {/* 2. Metrics Row */}
      <section className="metrics-grid">
        <article className="metric-card">
          <div className="metric-top">
            <span>REGISTERED SERVICES</span>
            <span className="metric-icon blue">📦</span>
          </div>
          <div className="metric-value">{deduplicatedServices.length}</div>
          <div className="metric-detail">
            <span className="metric-dot blue" />
            {deduplicatedServices.filter((s) => s.service_type === "cloud_run").length} Cloud Run services
          </div>
        </article>

        <article className="metric-card">
          <div className="metric-top">
            <span>DEPENDENCY RELATIONSHIPS</span>
            <span className="metric-icon violet">☊</span>
          </div>
          <div className="metric-value">{deduplicatedDependencies.length}</div>
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
          <div className="metric-value">{impactData?.total_impacted ?? 0}</div>
          <div className="metric-detail">
            <span className="metric-dot amber" />
            {selectedService ? `If ${selectedService} degrades` : "Select a service to analyze"}
          </div>
        </article>
      </section>

      {/* 3. Main Topology Visualizer + Deep Inspection Panel */}
      <div className="dependency-layout">
        {/* Graph Card */}
        <div className="dependency-graph-card">
          {/* Toolbar */}
          <div className="graph-toolbar">
            <div className="graph-controls-left">
              <label htmlFor="service-focus-select" className="graph-select-label">
                Focus Root:
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
                {deduplicatedServices.map((s) => (
                  <option key={s.name} value={s.name}>
                    {s.name} ({s.service_type})
                  </option>
                ))}
              </select>

              <label htmlFor="env-filter-select" className="graph-select-label" style={{ marginLeft: "8px" }}>
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

              {/* Focus Mode Chips */}
              <div className="graph-viewmode-pills" style={{ marginLeft: "10px" }}>
                <button
                  type="button"
                  className={`filter-chip ${focusMode === "focus" ? "active" : ""}`}
                  onClick={() => setFocusMode("focus")}
                  title="Show selected service and direct callers/dependencies"
                >
                  🎯 Focus (1-Hop)
                </button>
                <button
                  type="button"
                  className={`filter-chip ${focusMode === "extended" ? "active" : ""}`}
                  onClick={() => setFocusMode("extended")}
                  title="Show selected service and 2-hop radius"
                >
                  🔍 Extended (2-Hop)
                </button>
                <button
                  type="button"
                  className={`filter-chip ${focusMode === "all" ? "active" : ""}`}
                  onClick={() => setFocusMode("all")}
                  title="Show all registered services in environment"
                >
                  🌐 All Services ({deduplicatedServices.length})
                </button>
              </div>

              {/* Service Catalog Quick Search */}
              <div className="service-search-box">
                <span className="search-icon">🔍</span>
                <input
                  type="text"
                  className="service-search-input"
                  placeholder="Filter / Find service…"
                  value={serviceSearch}
                  onChange={(e) => {
                    setServiceSearch(e.target.value);
                    if (e.target.value && focusMode !== "all") {
                      setFocusMode("all");
                    }
                  }}
                  aria-label="Filter or find service"
                />
                {serviceSearch && (
                  <button
                    type="button"
                    className="search-clear-btn"
                    onClick={() => setServiceSearch("")}
                    title="Clear search"
                  >
                    ✕
                  </button>
                )}
              </div>
            </div>

            {/* Viewport and Canvas Controls */}
            <div className="graph-controls-right">
              <button
                type="button"
                className={`action-btn-sm ${showLabels ? "active-toggle" : ""}`}
                onClick={() => setShowLabels((v) => !v)}
                title="Toggle relationship type labels directly on canvas"
              >
                🏷️ {showLabels ? "Hide Labels" : "Show Labels"}
              </button>

              <div className="graph-zoom-controls">
                <button
                  type="button"
                  className="zoom-btn"
                  onClick={handleZoomIn}
                  title="Zoom In"
                  aria-label="Zoom In"
                >
                  ＋
                </button>
                <span className="zoom-level-indicator" title="Current Zoom Level">
                  {Math.round(zoom * 100)}%
                </span>
                <button
                  type="button"
                  className="zoom-btn"
                  onClick={handleZoomOut}
                  title="Zoom Out"
                  aria-label="Zoom Out"
                >
                  −
                </button>
                <button
                  type="button"
                  className="zoom-btn reset"
                  onClick={() => fitToView()}
                  title="Fit to View"
                >
                  ⛶ Fit to View
                </button>
                {focusMode !== "focus" && (
                  <button
                    type="button"
                    className="zoom-btn focus-reset"
                    onClick={handleResetToFocus}
                    title="Reset to 1-hop focus view"
                  >
                    ↺ Reset to Focus
                  </button>
                )}
              </div>
            </div>
          </div>

          {/* Visibility Banner */}
          <div className="graph-status-bar">
            <span className="graph-status-count">
              Showing <strong>{visibleServices.length}</strong> of <strong>{focusMode === "all" ? totalMatchingInAllMode : deduplicatedServices.length}</strong> Services · <strong>{visibleDependencies.length}</strong> Relationships
            </span>
            {focusMode === "all" && visibleServices.length < totalMatchingInAllMode && (
              <span className="batch-controls">
                <button
                  type="button"
                  className="secondary-button compact"
                  onClick={() => setAllModeLimit((prev) => prev + 50)}
                  title="Load next 50 services"
                >
                  + Load 50 More
                </button>
                <button
                  type="button"
                  className="secondary-button compact"
                  onClick={() => setAllModeLimit(totalMatchingInAllMode)}
                  title={`Load all ${totalMatchingInAllMode} services`}
                >
                  Load All ({totalMatchingInAllMode})
                </button>
              </span>
            )}
            {focusMode !== "all" && hiddenServiceCount > 0 && (
              <span className="graph-status-hint">
                Focused on <strong>{selectedService}</strong> ({selectedDownstreamCallers.length} callers, {selectedUpstreamDeps.length} dependencies). {hiddenServiceCount} distant services hidden.
                <button
                  type="button"
                  className="inline-link-btn"
                  onClick={() => setFocusMode("all")}
                  style={{ marginLeft: "8px" }}
                >
                  Show all services →
                </button>
              </span>
            )}
          </div>

          {/* SVG Diagram Canvas */}
          <div
            className="svg-canvas-container"
            ref={svgWrapperRef}
            onMouseDown={handleMouseDown}
            onMouseMove={handleMouseMove}
            onMouseUp={handleMouseUp}
            style={{ cursor: isDragging ? "grabbing" : "default" }}
          >
            {loading ? (
              <div className="timeline-empty">
                <div className="spinner" style={{ marginBottom: "12px" }} />
                <p>Loading service dependency graph...</p>
              </div>
            ) : visibleServices.length === 0 ? (
              <div className="timeline-empty">
                <p>No services registered for this environment.</p>
              </div>
            ) : (
              <svg
                className="dependency-svg"
                viewBox={`0 0 ${containerSize.width} ${containerSize.height}`}
              >
                <defs>
                  {/* Verified Arrow Marker (Emerald) */}
                  <marker
                    id="arrow-verified"
                    viewBox="0 0 10 10"
                    refX="9"
                    refY="5"
                    markerWidth="6"
                    markerHeight="6"
                    orient="auto-start-reverse"
                  >
                    <path d="M 0 1.5 L 10 5 L 0 8.5 z" fill="var(--graph-edge-verified)" />
                  </marker>

                  {/* Configured Arrow Marker (Cobalt Blue) */}
                  <marker
                    id="arrow-configured"
                    viewBox="0 0 10 10"
                    refX="9"
                    refY="5"
                    markerWidth="6"
                    markerHeight="6"
                    orient="auto-start-reverse"
                  >
                    <path d="M 0 1.5 L 10 5 L 0 8.5 z" fill="var(--graph-edge-configured)" />
                  </marker>

                  {/* Impacted Arrow Marker (Warm Amber) */}
                  <marker
                    id="arrow-impacted"
                    viewBox="0 0 10 10"
                    refX="9"
                    refY="5"
                    markerWidth="6"
                    markerHeight="6"
                    orient="auto-start-reverse"
                  >
                    <path d="M 0 1.5 L 10 5 L 0 8.5 z" fill="var(--graph-edge-inferred)" />
                  </marker>

                  {/* Selected Arrow Marker (Cyan) */}
                  <marker
                    id="arrow-selected"
                    viewBox="0 0 10 10"
                    refX="9"
                    refY="5"
                    markerWidth="6"
                    markerHeight="6"
                    orient="auto-start-reverse"
                  >
                    <path d="M 0 1.5 L 10 5 L 0 8.5 z" fill="var(--brand-primary)" />
                  </marker>

                </defs>

                {/* Canvas Background for Pan Dragging */}
                <rect
                  className="canvas-bg"
                  x="0"
                  y="0"
                  width="100%"
                  height="100%"
                  fill="transparent"
                />

                {/* Zoomable & Pannable Scene */}
                <g transform={`translate(${pan.x}, ${pan.y}) scale(${zoom})`}>
                  {/* 1. Render Directional Curved Edges */}
                  {visibleDependencies.map((dep) => {
                    const sourcePos = nodePositions[dep.source];
                    const targetPos = nodePositions[dep.target];
                    if (!sourcePos || !targetPos) return null;

                    const isSelected =
                      selectedDependency?.source === dep.source &&
                      selectedDependency?.target === dep.target;

                    const isConnectedToSelected =
                      selectedService &&
                      (dep.source === selectedService || dep.target === selectedService);

                    const isImpacted =
                      impactedServiceMap.has(dep.source) && dep.target === selectedService;

                    const isDimmed =
                      selectedService &&
                      !isConnectedToSelected &&
                      dep.source !== selectedService &&
                      dep.target !== selectedService;

                    const isHovered =
                      hoveredDependency?.source === dep.source &&
                      hoveredDependency?.target === dep.target;

                    return (
                      <DependencyGraphEdge
                        key={`${dep.source}->${dep.target}`}
                        edge={dep}
                        sourcePos={sourcePos}
                        targetPos={targetPos}
                        isSelected={isSelected}
                        isConnectedToSelected={isConnectedToSelected}
                        isImpacted={isImpacted}
                        isDimmed={isDimmed}
                        showLabelAlways={showLabels}
                        isHovered={isHovered}
                        onSelect={handleSelectEdge}
                        onHover={handleHoverEdge}
                      />
                    );
                  })}

                  {/* 2. Render Unique Service Nodes */}
                  {visibleServices.map((svc) => {
                    const pos = nodePositions[svc.name];
                    if (!pos) return null;

                    const isSelected = svc.name === selectedService;
                    const isFocusRoot = svc.name === selectedService && focusMode === "focus";
                    const impactInfo = impactedServiceMap.get(svc.name);
                    const isImpacted = !!impactInfo;
                    const isCaller = callerSet.has(svc.name);
                    const isDependency = depSet.has(svc.name);

                    const isDimmed =
                      selectedService &&
                      !isSelected &&
                      !isCaller &&
                      !isDependency;

                    return (
                      <DependencyGraphNode
                        key={svc.name}
                        node={svc}
                        pos={pos}
                        isSelected={isSelected}
                        isImpacted={isImpacted}
                        impactLevel={impactInfo?.impact_level}
                        isFocusRoot={isFocusRoot}
                        isCaller={isCaller}
                        isDependency={isDependency}
                        isDimmed={isDimmed}
                        onSelect={handleSelectService}
                        onHover={handleHoverService}
                      />
                    );
                  })}
                </g>
              </svg>
            )}
          </div>

          {/* Legend */}
          <div className="graph-footer-legend">
            <span className="legend-item">
              <span className="legend-dot healthy" /> Healthy
            </span>
            <span className="legend-item">
              <span className="legend-dot degraded" /> Degraded
            </span>
            <span className="legend-item">
              <span className="legend-dot unhealthy" /> Unhealthy
            </span>
            <span className="legend-item">
              <span className="legend-dot selected" /> Selected
            </span>
            <span className="legend-item">
              <span className="legend-line verified" /> Verified Evidence
            </span>
            <span className="legend-item">
              <span className="legend-line configured" /> Configured Architecture
            </span>
          </div>
        </div>

        {/* 4. Right Deep Inspection & Cascading Impact Panel */}
        <div className="dependency-inspector-panel">
          {selectedService ? (
            <div className="inspector-content">
              {/* Service Header */}
              <div className="service-inspector-header">
                <div className="service-inspector-eyebrow">
                  <span className="eyebrow-text">SERVICE DETAILS & IMPACT</span>
                  <div className="eyebrow-divider" aria-hidden="true" />
                </div>

                <div className="service-inspector-title-row">
                  <span className="service-inspector-icon" aria-hidden="true">
                    {SERVICE_TYPE_ICONS[selectedServiceObj?.service_type] || "📦"}
                  </span>
                  <h2 className="service-inspector-name" title={selectedService}>
                    {selectedService}
                  </h2>
                </div>

                <div className="service-inspector-badges">
                  <span className="service-type-badge" title="Service Type">
                    {SERVICE_TYPE_LABELS[selectedServiceObj?.service_type] || selectedServiceObj?.service_type || "microservice"}
                  </span>
                  <span className="env-badge" title="Deployment Environment">
                    <span className="env-label-prefix">ENV:</span> {selectedServiceObj?.environment || "production"}
                  </span>
                  <span
                    className={`health-badge ${
                      selectedServiceObj?.health_status || "unknown"
                    }`}
                    title={`Health Status: ${selectedServiceObj?.health_status || "unknown"}`}
                  >
                    <span className="health-dot" aria-hidden="true" />
                    {selectedServiceObj?.health_status || "unknown"}
                  </span>
                </div>
              </div>

              {/* Selected Relationship Deep Inspector (Displayed when an edge is clicked) */}
              {selectedDependency && (
                <div className="relationship-inspector-card">
                  <div className="relationship-card-top">
                    <span className="rel-title">RELATIONSHIP EVIDENCE</span>
                    <button
                      type="button"
                      className="close-button-compact"
                      onClick={() => setSelectedDependency(null)}
                      title="Close relationship view"
                    >
                      ✕
                    </button>
                  </div>

                  <div className="rel-direction-row">
                    <strong>{selectedDependency.source}</strong>
                    <span className="rel-arrow">──►</span>
                    <strong>{selectedDependency.target}</strong>
                  </div>

                  <div className="rel-meta-row">
                    <span>
                      Type: <code>{selectedDependency.relationship_type}</code>
                    </span>
                    <span className={`status-pill ${selectedDependency.verification_status}`}>
                      {selectedDependency.verification_status}
                    </span>
                  </div>

                  {selectedDependency.confidence && (
                    <div className="rel-meta-row" style={{ fontSize: "11px", color: "#8d9ab3" }}>
                      <span>Confidence:</span>
                      <strong style={{ color: "#e2e8f0" }}>{selectedDependency.confidence.toUpperCase()}</strong>
                    </div>
                  )}

                  <div className="rel-evidence-box">
                    <span className="rel-evidence-label">VERBATIM EVIDENCE SOURCE:</span>
                    <p>{selectedDependency.evidence}</p>
                  </div>

                  <div className="rel-actions-row">
                    <button
                      type="button"
                      className="action-btn-sm"
                      onClick={() => {
                        setSelectedService(selectedDependency.source);
                        setSelectedDependency(null);
                      }}
                    >
                      Focus Source ({selectedDependency.source}) →
                    </button>
                    <button
                      type="button"
                      className="action-btn-sm"
                      onClick={() => {
                        setSelectedService(selectedDependency.target);
                        setSelectedDependency(null);
                      }}
                    >
                      Focus Target ({selectedDependency.target}) →
                    </button>
                  </div>
                </div>
              )}

              {/* Cascading Downstream Impact Section */}
              <div className="inspector-section">
                <div className="section-title-row">
                  <h3>Cascading Downstream Impact</h3>
                  <span className="impact-count-pill">
                    {impactData?.total_impacted ?? 0} AFFECTED
                  </span>
                </div>
                <p className="inspector-help">
                  Services that depend directly or transitively on <strong>{selectedService}</strong> and may be impacted if it degrades.
                </p>

                {impactLoading ? (
                  <p className="muted">Analyzing dependency paths...</p>
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
                          <span>
                            Depth {item.depth} ({item.depth === 1 ? "Direct caller" : "Transitive"})
                          </span>
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
                        <div style={{ marginTop: "4px" }}>
                          <button
                            type="button"
                            className="inline-link-btn"
                            onClick={() => {
                              setSelectedService(item.service_name);
                              setSelectedDependency(null);
                            }}
                          >
                            Jump to {item.service_name} in graph →
                          </button>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>

              {/* Upstream Dependencies (Called by selectedService) */}
              <div className="inspector-section">
                <div className="section-title-row">
                  <h3>Dependencies Required by {selectedService}</h3>
                  <span className="dep-count-tag">{selectedUpstreamDeps.length}</span>
                </div>
                <p className="inspector-help">
                  Services that <strong>{selectedService}</strong> calls to perform its operations.
                </p>

                {selectedUpstreamDeps.length === 0 ? (
                  <p className="muted">No upstream dependencies declared.</p>
                ) : (
                  <div className="dependency-list">
                    {selectedUpstreamDeps.map((dep) => (
                      <div
                        key={dep.id || `${dep.source}->${dep.target}`}
                        className={`dep-item-card ${
                          selectedDependency?.source === dep.source &&
                          selectedDependency?.target === dep.target
                            ? "selected"
                            : ""
                        }`}
                        onClick={() => setSelectedDependency(dep)}
                      >
                        <div className="dep-item-top">
                          <span className="dep-target-name">→ {dep.target}</span>
                          <span className={`status-pill ${dep.verification_status}`}>
                            {dep.verification_status}
                          </span>
                        </div>
                        <div className="dep-item-type">
                          Type: <code>{dep.relationship_type}</code> · Confidence: {dep.confidence || "high"}
                        </div>
                        <div className="dep-item-evidence">{dep.evidence}</div>
                        <div className="dep-card-action">
                          <button
                            type="button"
                            className="inline-link-btn"
                            onClick={(e) => {
                              e.stopPropagation();
                              setSelectedService(dep.target);
                              setSelectedDependency(null);
                            }}
                          >
                            Inspect {dep.target} →
                          </button>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>

              {/* Downstream Callers (Depend on selectedService) */}
              <div className="inspector-section">
                <div className="section-title-row">
                  <h3>Direct Callers (Depend on {selectedService})</h3>
                  <span className="dep-count-tag">{selectedDownstreamCallers.length}</span>
                </div>
                <p className="inspector-help">
                  Services that call <strong>{selectedService}</strong> directly.
                </p>

                {selectedDownstreamCallers.length === 0 ? (
                  <p className="muted">No direct callers declared.</p>
                ) : (
                  <div className="dependency-list">
                    {selectedDownstreamCallers.map((dep) => (
                      <div
                        key={dep.id || `${dep.source}->${dep.target}`}
                        className={`dep-item-card ${
                          selectedDependency?.source === dep.source &&
                          selectedDependency?.target === dep.target
                            ? "selected"
                            : ""
                        }`}
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
                        <div className="dep-card-action">
                          <button
                            type="button"
                            className="inline-link-btn"
                            onClick={(e) => {
                              e.stopPropagation();
                              setSelectedService(dep.source);
                              setSelectedDependency(null);
                            }}
                          >
                            Inspect {dep.source} →
                          </button>
                        </div>
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
        </div>
      </div>

      {/* 5. Add Dependency Modal */}
      {showAddModal && (
        <div className="modal-overlay" onClick={() => setShowAddModal(false)}>
          <div className="modal-window" onClick={(e) => e.stopPropagation()}>
            <div className="modal-top">
              <h3>Configure Service Dependency</h3>
              <button
                type="button"
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
                  {deduplicatedServices.map((s) => (
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
