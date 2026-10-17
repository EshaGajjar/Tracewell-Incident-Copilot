import { useState, useMemo } from "react";
import {
  RefreshCwIcon,
  PlusIcon,
  SearchIcon,
  CheckCircleIcon,
  ClockIcon,
  AlertCircleIcon,
  ChevronRightIcon,
  FilterIcon,
} from "./Icons.jsx";

function prettyStatus(value = "open") {
  return value.replaceAll("_", " ");
}

export function parseTimestamp(value) {
  if (!value) return null;
  if (value instanceof Date) {
    return Number.isNaN(value.getTime()) ? null : value;
  }
  if (typeof value === "object") {
    const sec = value.seconds ?? value._seconds;
    if (sec != null) {
      const ms = sec * 1000 + (value.nanoseconds || value._nanoseconds || 0) / 1000000;
      const d = new Date(ms);
      return Number.isNaN(d.getTime()) ? null : d;
    }
  }
  if (typeof value === "number") {
    const ms = value < 100000000000 ? value * 1000 : value;
    const d = new Date(ms);
    return Number.isNaN(d.getTime()) ? null : d;
  }
  if (typeof value === "string") {
    let str = value.trim();
    if (!str) return null;
    let d = new Date(str);
    if (!Number.isNaN(d.getTime())) {
      // If timestamp falls in the future by > 1 minute, check if local wall-clock interpretation places it in the past
      const now = new Date();
      if (d.getTime() - now.getTime() > 60000 && (str.endsWith("Z") || /[+-]\d{2}:?\d{2}$/.test(str))) {
        const strNoTz = str.replace(/Z|[+-]\d{2}:?\d{2}$/, "");
        const dLocal = new Date(strNoTz);
        if (!Number.isNaN(dLocal.getTime()) && now.getTime() - dLocal.getTime() >= 0) {
          return dLocal;
        }
      }
      return d;
    }
    if (str.includes(" ") && !str.includes("T")) {
      str = str.replace(" ", "T");
      d = new Date(str);
      if (!Number.isNaN(d.getTime())) return d;
    }
    if (!str.endsWith("Z") && !/[+-]\d{2}:?\d{2}$/.test(str)) {
      d = new Date(`${str}Z`);
      if (!Number.isNaN(d.getTime())) return d;
    }
  }
  return null;
}

export function relativeTime(value) {
  if (!value) return "—";
  const date = parseTimestamp(value);
  if (!date) return "—";

  const now = Date.now();
  const diffMs = now - date.getTime();

  // Tolerate clock skew within +/- 60 seconds
  if (diffMs >= -60000 && diffMs < 60000) {
    return "Just now";
  }

  if (diffMs < -60000) {
    return date.toLocaleDateString(undefined, {
      month: "short",
      day: "numeric",
      year: "numeric",
    });
  }

  const seconds = Math.floor(diffMs / 1000);
  const minutes = Math.floor(seconds / 60);
  if (minutes < 1) return "Just now";
  if (minutes < 60) {
    return `${minutes} ${minutes === 1 ? "minute" : "minutes"} ago`;
  }

  const hours = Math.floor(minutes / 60);
  if (hours < 24) {
    return `${hours} ${hours === 1 ? "hour" : "hours"} ago`;
  }

  const days = Math.floor(hours / 24);
  if (days < 30) {
    return `${days} ${days === 1 ? "day" : "days"} ago`;
  }

  return date.toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

export function formatFullTimestamp(value) {
  const date = parseTimestamp(value);
  if (!date) return "";
  return date.toLocaleString();
}

export default function IncidentList({
  items = [],
  loading = false,
  error = "",
  onRefresh,
  onOpen,
  onCreate,
  externalQuery = "",
  filter: controlledFilter,
  onFilterChange,
}) {
  const [internalFilter, setInternalFilter] = useState("all");
  const filter = controlledFilter !== undefined ? controlledFilter : internalFilter;
  const setFilter = onFilterChange || setInternalFilter;

  const [severityFilter, setSeverityFilter] = useState("all");
  const [serviceFilter, setServiceFilter] = useState("all");
  const [sortOrder, setSortOrder] = useState("newest");
  const [localQuery, setLocalQuery] = useState("");

  const effectiveQuery = (externalQuery || localQuery).trim().toLowerCase();

  // Extract distinct service names
  const availableServices = useMemo(() => {
    const set = new Set();
    items.forEach((i) => {
      if (i.service) set.add(i.service);
    });
    return Array.from(set).sort();
  }, [items]);

  const visible = useMemo(() => {
    return items
      .filter((i) => {
        // Status filter
        if (filter === "active") {
          if (["closed", "resolved", "rejected"].includes(i.status)) return false;
        } else if (filter === "awaiting_approval") {
          if (i.status !== "awaiting_approval" && i.pending_action?.status !== "pending") return false;
        } else if (filter === "closed") {
          if (!["closed", "resolved"].includes(i.status)) return false;
        } else if (filter !== "all") {
          if (i.status !== filter) return false;
        }

        // Severity filter
        if (severityFilter !== "all" && i.severity !== severityFilter) {
          return false;
        }

        // Service filter
        if (serviceFilter !== "all" && i.service !== serviceFilter) {
          return false;
        }

        // Search query
        if (effectiveQuery) {
          const text = `${i.title || ""} ${i.service || ""} ${i.id || ""} ${i.description || ""}`.toLowerCase();
          if (!text.includes(effectiveQuery)) return false;
        }

        return true;
      })
      .sort((a, b) => {
        if (sortOrder === "newest") {
          const timeB = parseTimestamp(b.created_at || b.createdAt || b.reported_at || b.timestamp)?.getTime() || 0;
          const timeA = parseTimestamp(a.created_at || a.createdAt || a.reported_at || a.timestamp)?.getTime() || 0;
          return timeB - timeA;
        }
        if (sortOrder === "oldest") {
          const timeA = parseTimestamp(a.created_at || a.createdAt || a.reported_at || a.timestamp)?.getTime() || 0;
          const timeB = parseTimestamp(b.created_at || b.createdAt || b.reported_at || b.timestamp)?.getTime() || 0;
          return timeA - timeB;
        }
        if (sortOrder === "severity") {
          const rank = { SEV1: 1, SEV2: 2, SEV3: 3, SEV4: 4 };
          return (rank[a.severity] || 5) - (rank[b.severity] || 5);
        }
        return 0;
      });
  }, [items, filter, severityFilter, serviceFilter, effectiveQuery, sortOrder]);

  return (
    <div className="incident-section">
      {/* Section Header */}
      <div className="section-heading">
        <div>
          <h2>
            <span>Incident Queue</span>
            <span className="count-badge">{visible.length} of {items.length}</span>
          </h2>
          <p className="muted">
            All reported events, signals, and root-cause investigations.
          </p>
        </div>
        <div className="section-actions">
          <button
            className="secondary-button compact"
            onClick={onRefresh}
            disabled={loading}
            aria-label="Refresh incidents"
            title="Refresh incident list"
          >
            <RefreshCwIcon size={13} className={loading ? "spin" : ""} />
            <span>{loading ? "Refreshing…" : "Refresh"}</span>
          </button>
          <button className="primary-button compact" onClick={onCreate}>
            <PlusIcon size={13} />
            <span>New Incident</span>
          </button>
        </div>
      </div>

      {/* Toolbar with Filters */}
      <div className="list-toolbar">
        <div className="filter-tabs">
          {[
            ["all", "All Incidents"],
            ["active", "Active"],
            ["awaiting_approval", "Needs Approval"],
            ["closed", "Resolved"],
          ].map(([key, label]) => (
            <button
              key={key}
              className={filter === key ? "filter-tab active" : "filter-tab"}
              onClick={() => setFilter(key)}
            >
              {label}
            </button>
          ))}
        </div>

        <div className="toolbar-controls">
          {/* Severity Dropdown */}
          <select
            className="select-filter"
            value={severityFilter}
            onChange={(e) => setSeverityFilter(e.target.value)}
            aria-label="Filter by severity"
          >
            <option value="all">All Severities</option>
            <option value="SEV1">SEV1 (Critical)</option>
            <option value="SEV2">SEV2 (High)</option>
            <option value="SEV3">SEV3 (Medium)</option>
            <option value="SEV4">SEV4 (Low)</option>
          </select>

          {/* Service Dropdown */}
          <select
            className="select-filter"
            value={serviceFilter}
            onChange={(e) => setServiceFilter(e.target.value)}
            aria-label="Filter by service"
          >
            <option value="all">All Services</option>
            {availableServices.map((svc) => (
              <option key={svc} value={svc}>
                {svc}
              </option>
            ))}
          </select>

          {/* Sort Dropdown */}
          <select
            className="select-filter"
            value={sortOrder}
            onChange={(e) => setSortOrder(e.target.value)}
            aria-label="Sort order"
          >
            <option value="newest">Newest first</option>
            <option value="oldest">Oldest first</option>
            <option value="severity">Highest severity</option>
          </select>

          {/* Inline search box if not using global */}
          {!externalQuery && (
            <label className="search-box">
              <SearchIcon size={13} />
              <input
                value={localQuery}
                onChange={(e) => setLocalQuery(e.target.value)}
                placeholder="Filter table…"
              />
            </label>
          )}
        </div>
      </div>

      {/* Error Notice with Retry Action */}
      {error && (
        <div
          className="notice error-notice"
          role="alert"
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            gap: "12px",
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
            <AlertCircleIcon size={15} />
            <span>{error}</span>
          </div>
          {onRefresh && (
            <button
              type="button"
              className="secondary-button compact"
              onClick={onRefresh}
              style={{ flexShrink: 0 }}
            >
              <RefreshCwIcon size={12} />
              <span>Retry</span>
            </button>
          )}
        </div>
      )}

      {/* Table Content */}
      {loading && !items.length ? (
        <IncidentTableSkeleton />
      ) : visible.length === 0 ? (
        <div className="empty-state">
          <div className="empty-icon">
            <FilterIcon size={24} />
          </div>
          <strong>
            {items.length ? "No matching incidents" : "Your incident queue is clear"}
          </strong>
          <span>
            {items.length
              ? "Try adjusting your search or active filters."
              : "Report an incident to start an automated evidence-backed investigation."}
          </span>
          {!items.length && (
            <button
              className="primary-button"
              style={{ marginTop: "12px" }}
              onClick={onCreate}
            >
              <PlusIcon size={14} />
              <span>Report First Incident</span>
            </button>
          )}
        </div>
      ) : (
        <div className="incident-table-wrap">
          <table className="incident-table">
            <thead>
              <tr>
                <th>INCIDENT</th>
                <th>SERVICE</th>
                <th>SEVERITY</th>
                <th>STATUS</th>
                <th>INVESTIGATION</th>
                <th>REPORTED</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {visible.map((i) => {
                const isInvestigated = Boolean(
                  i.diagnosis || i.root_cause || (i.metadata && i.metadata.investigated)
                );
                const glyphTone =
                  i.status === "awaiting_approval"
                    ? "amber"
                    : i.status === "closed" || i.status === "resolved"
                    ? "green"
                    : "blue";

                return (
                  <tr
                    key={i.id}
                    onClick={() => onOpen(i.id)}
                    tabIndex={0}
                    onKeyDown={(e) => e.key === "Enter" && onOpen(i.id)}
                    aria-label={`Open incident ${i.title}`}
                  >
                    <td>
                      <div className="incident-title-cell">
                        <span className={`incident-glyph ${glyphTone}`}>
                          {i.status === "awaiting_approval" ? (
                            <ClockIcon size={12} />
                          ) : i.status === "closed" || i.status === "resolved" ? (
                            <CheckCircleIcon size={12} />
                          ) : (
                            <AlertCircleIcon size={12} />
                          )}
                        </span>
                        <div>
                          <strong>{i.title || "Untitled incident"}</strong>
                          <small>{i.id}</small>
                        </div>
                      </div>
                    </td>
                    <td>
                      <span className="service-tag">{i.service || "unknown"}</span>
                    </td>
                    <td>
                      <span
                        className={`severity-pill ${(
                          i.severity || "SEV3"
                        ).toLowerCase()}`}
                      >
                        <span />
                        {i.severity || "SEV3"}
                      </span>
                    </td>
                    <td>
                      <span
                        className={`status-pill ${(
                          i.status || "open"
                        ).replaceAll("_", "-")}`}
                      >
                        <span />
                        {prettyStatus(i.status)}
                      </span>
                    </td>
                    <td>
                      <span
                        className={`inv-progress-badge ${
                          isInvestigated ? "done" : "pending"
                        }`}
                      >
                        {isInvestigated ? (
                          <>
                            <CheckCircleIcon size={13} />
                            <span>Investigated</span>
                          </>
                        ) : (
                          <>
                            <ClockIcon size={13} />
                            <span>Pending</span>
                          </>
                        )}
                      </span>
                    </td>
                    <td
                      className="time-cell"
                      title={formatFullTimestamp(i.created_at || i.createdAt || i.reported_at || i.timestamp)}
                    >
                      {relativeTime(i.created_at || i.createdAt || i.reported_at || i.timestamp)}
                    </td>
                    <td className="row-arrow">
                      <ChevronRightIcon size={14} />
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {/* Table Footer */}
      <div className="list-foot">
        <span>
          {loading ? "Syncing telemetry…" : "Continuous live telemetry sync active"}
        </span>
        <span>
          Showing {visible.length} of {items.length} records
        </span>
      </div>
    </div>
  );
}

function IncidentTableSkeleton() {
  return (
    <div className="incident-table-wrap">
      <table className="incident-table" aria-label="Loading incidents table skeleton">
        <thead>
          <tr>
            <th>INCIDENT</th>
            <th>SERVICE</th>
            <th>SEVERITY</th>
            <th>STATUS</th>
            <th>INVESTIGATION</th>
            <th>REPORTED</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          {[1, 2, 3, 4, 5].map((idx) => (
            <tr key={idx} className="skeleton-table-row">
              <td>
                <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
                  <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                    <span
                      className="skeleton-line"
                      style={{ width: "68px", height: "16px", borderRadius: "4px" }}
                    />
                    <span
                      className="skeleton-line"
                      style={{ width: "160px", height: "16px", borderRadius: "4px" }}
                    />
                  </div>
                  <span
                    className="skeleton-line"
                    style={{ width: "220px", height: "12px", borderRadius: "3px" }}
                  />
                </div>
              </td>
              <td>
                <span
                  className="skeleton-line"
                  style={{ width: "88px", height: "20px", borderRadius: "4px" }}
                />
              </td>
              <td>
                <span
                  className="skeleton-line"
                  style={{ width: "54px", height: "20px", borderRadius: "999px" }}
                />
              </td>
              <td>
                <span
                  className="skeleton-line"
                  style={{ width: "74px", height: "20px", borderRadius: "999px" }}
                />
              </td>
              <td>
                <span
                  className="skeleton-line"
                  style={{ width: "112px", height: "20px", borderRadius: "4px" }}
                />
              </td>
              <td>
                <span
                  className="skeleton-line"
                  style={{ width: "72px", height: "14px", borderRadius: "4px" }}
                />
              </td>
              <td>
                <span
                  className="skeleton-line"
                  style={{ width: "14px", height: "14px", borderRadius: "4px" }}
                />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

