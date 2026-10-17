import { useCallback, useEffect, useState, useRef } from "react";
import IncidentList from "./components/IncidentList.jsx";
import IncidentDetail from "./components/IncidentDetail.jsx";
import ReportForm from "./components/ReportForm.jsx";
import ServiceDependencyView from "./components/ServiceDependencyView.jsx";
import ErrorBoundary from "./components/ErrorBoundary.jsx";
import { API_URL, request } from "./firebase.js";
import { useTheme } from "./theme.js";
import {
  OverviewIcon,
  DependencyIcon,
  PlusIcon,
  SearchIcon,
  SunIcon,
  MoonIcon,
  SidebarCollapseIcon,
  SidebarExpandIcon,
  ActivityIcon,
} from "./components/Icons.jsx";
import { TracewellLogo } from "./components/TracewellLogo.jsx";
import { LaunchScreen } from "./components/LaunchScreen.jsx";

export default function App() {
  const [view, setView] = useState({ name: "list" });
  const [incidents, setIncidents] = useState([]);
  const [lastUpdated, setLastUpdated] = useState(null);
  const [incidentsLoading, setIncidentsLoading] = useState(true);
  const [incidentsError, setIncidentsError] = useState("");
  const [globalSearch, setGlobalSearch] = useState("");
  const [listFilter, setListFilter] = useState("all");
  const searchInputRef = useRef(null);

  // Theme hook
  const { theme, setTheme } = useTheme();

  // Collapsible sidebar state
  const [sidebarCollapsed, setSidebarCollapsed] = useState(() => {
    try {
      return localStorage.getItem("tracewell-sidebar-collapsed") === "true";
    } catch {
      return false;
    }
  });

  const toggleSidebar = () => {
    setSidebarCollapsed((prev) => {
      const next = !prev;
      try {
        localStorage.setItem("tracewell-sidebar-collapsed", String(next));
      } catch {}
      return next;
    });
  };

  // Keyboard shortcut: ⌘K or Ctrl+K to focus search
  useEffect(() => {
    const handleKeyDown = (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "k") {
        e.preventDefault();
        if (searchInputRef.current) {
          searchInputRef.current.focus();
        }
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, []);

  // Fetch incidents
  const refreshIncidents = useCallback(async () => {
    setIncidentsLoading(true);
    setIncidentsError("");
    try {
      const rows = await request("/incidents");
      setIncidents(Array.isArray(rows) ? rows : []);
      setLastUpdated(new Date());
    } catch (error) {
      setIncidentsError(error?.message || "Could not load incidents");
    } finally {
      setIncidentsLoading(false);
    }
  }, []);

  useEffect(() => {
    refreshIncidents();
  }, [refreshIncidents]);

  const activeCount = incidents.filter(
    (i) => !["closed", "resolved", "rejected"].includes(i.status)
  ).length;
  const awaiting = incidents.filter(
    (i) => i.status === "awaiting_approval" || (i.pending_action && i.pending_action.status === "pending")
  ).length;
  const critical = incidents.filter(
    (i) => ["SEV1", "SEV2"].includes(i.severity) && !["closed", "resolved", "rejected"].includes(i.status)
  ).length;

  return (
    <div className={`app-shell ${sidebarCollapsed ? "sidebar-collapsed" : ""}`}>
      {/* ---------------- Branded Website Launch Screen ---------------- */}
      <LaunchScreen isReady={!incidentsLoading} />

      {/* ---------------- Collapsible Sidebar ---------------- */}
      <aside className="sidebar">
        <div className="sidebar-brand-row">
          <button
            className="brand"
            onClick={() => setView({ name: "list" })}
            aria-label="Tracewell home"
            title="Tracewell Incident Intelligence"
          >
            <TracewellLogo size={28} className="brand-logo" />
            <div className="brand-text">
              <strong>TRACEWELL</strong>
              <small>Incident Copilot</small>
            </div>
          </button>
          <button
            type="button"
            className="sidebar-toggle-btn"
            onClick={toggleSidebar}
            title={sidebarCollapsed ? "Expand sidebar" : "Collapse sidebar"}
            aria-label={sidebarCollapsed ? "Expand sidebar" : "Collapse sidebar"}
          >
            {sidebarCollapsed ? (
              <SidebarExpandIcon size={15} />
            ) : (
              <SidebarCollapseIcon size={15} />
            )}
          </button>
        </div>

        <div className="side-label">Workspace</div>

        <button
          className={`nav-item ${view.name === "list" ? "selected" : ""}`}
          onClick={() => setView({ name: "list" })}
          title="Incident Overview"
        >
          <span className="nav-icon">
            <OverviewIcon size={15} />
          </span>
          <span>Incident Overview</span>
          <span className="nav-count">{incidents.length}</span>
        </button>

        <button
          className={`nav-item ${view.name === "dependencies" ? "selected" : ""}`}
          onClick={() => setView({ name: "dependencies" })}
          title="Service Dependency Map"
        >
          <span className="nav-icon">
            <DependencyIcon size={15} />
          </span>
          <span>Service Map</span>
        </button>

        <button
          className={`nav-item ${view.name === "new" ? "selected" : ""}`}
          onClick={() => setView({ name: "new" })}
          title="Report Incident"
        >
          <span className="nav-icon">
            <PlusIcon size={15} />
          </span>
          <span>Report Incident</span>
        </button>

        <div className="sidebar-bottom">
          <div className="system-indicator" title="Connected environment">
            <span className="pulse-dot" />
            <div className="system-indicator-text">
              <strong>Local Development</strong>
              <small>{API_URL ? "Backend Connected" : "Local SQLite Mode"}</small>
            </div>
          </div>
          <div className="sidebar-note">
            Evidence-backed intelligence. Human approved.
          </div>
        </div>
      </aside>

      {/* ---------------- Main Content Area ---------------- */}
      <main className="main-area">
        {/* Compact Top Navigation Bar */}
        <header className="topbar">
          <div className="breadcrumbs">
            <button
              type="button"
              className="breadcrumb-btn"
              onClick={() => setView({ name: "list" })}
              title="Return to overview"
            >
              Workspace
            </button>
            <span className="separator">/</span>
            <strong>
              {view.name === "new"
                ? "New incident"
                : view.name === "detail"
                ? "Investigation detail"
                : view.name === "dependencies"
                ? "Service dependencies"
                : "Incident overview"}
            </strong>
          </div>

          <div className="topbar-center">
            <div className="global-search-bar">
              <SearchIcon size={14} />
              <input
                ref={searchInputRef}
                type="text"
                placeholder="Search incidents, services, or events..."
                value={globalSearch}
                onChange={(e) => {
                  setGlobalSearch(e.target.value);
                  if (view.name !== "list") {
                    setView({ name: "list" });
                  }
                }}
              />
              <span className="kbd-shortcut">⌘K</span>
            </div>
          </div>

          <div className="topbar-right">
            {/* Dark and Light Theme Selector */}
            <div
              className="theme-selector"
              role="radiogroup"
              aria-label="Color scheme preference"
            >
              <button
                type="button"
                className={`theme-btn ${theme === "light" ? "active" : ""}`}
                onClick={() => setTheme("light")}
                title="Light mode"
                aria-label="Light mode"
              >
                <SunIcon size={14} />
              </button>
              <button
                type="button"
                className={`theme-btn ${theme === "dark" ? "active" : ""}`}
                onClick={() => setTheme("dark")}
                title="Dark mode"
                aria-label="Dark mode"
              >
                <MoonIcon size={14} />
              </button>
            </div>

            <span className="live-pill">
              <span className="pulse-dot" /> Live Monitoring
            </span>

            <button
              className="primary-button compact"
              onClick={() => setView({ name: "new" })}
            >
              <PlusIcon size={13} />
              <span>Report incident</span>
            </button>
          </div>
        </header>

        {!API_URL && (
          <div className="notice warning" style={{ margin: "16px 32px 0 32px" }}>
            Backend URL is running in local SQLite fallback mode.
          </div>
        )}

        {/* ---------------- Overview View ---------------- */}
        {view.name === "list" && (
          <>
            <section className="welcome-row">
              <div>
                <div className="eyebrow">
                  OPERATIONS CENTER <span className="eyebrow-line" />
                </div>
                <h1>Incident Overview</h1>
                <p>Monitor, investigate, and remediate incidents with observable evidence.</p>
              </div>
              <div className="updated">
                {lastUpdated
                  ? `Updated ${lastUpdated.toLocaleTimeString([], {
                      hour: "2-digit",
                      minute: "2-digit",
                    })}`
                  : "Connecting…"}
              </div>
            </section>

            <section className="metrics-grid">
              {incidentsLoading && !incidents.length ? (
                <>
                  <MetricSkeleton />
                  <MetricSkeleton />
                  <MetricSkeleton />
                  <MetricSkeleton />
                </>
              ) : (
                <>
                  <Metric
                    label="TOTAL INCIDENTS"
                    value={incidents.length}
                    detail="All active & historical"
                    tone="blue"
                  />
                  <Metric
                    label="ACTIVE INCIDENTS"
                    value={activeCount}
                    detail="Under triage or investigation"
                    tone="violet"
                  />
                  <Metric
                    label="AWAITING DECISION"
                    value={awaiting}
                    detail="Human approval required"
                    tone="amber"
                  />
                  <Metric
                    label="CRITICAL SEVERITY"
                    value={critical}
                    detail="SEV1 · SEV2 needing action"
                    tone="rose"
                  />
                </>
              )}
            </section>

            <section className="content-panel">
              <IncidentList
                items={incidents}
                loading={incidentsLoading}
                error={incidentsError}
                onRefresh={refreshIncidents}
                onOpen={(id) => setView({ name: "detail", id })}
                onCreate={() => setView({ name: "new" })}
                externalQuery={globalSearch}
                filter={listFilter}
                onFilterChange={setListFilter}
              />
            </section>
          </>
        )}

        {/* ---------------- Service Dependencies View ---------------- */}
        {view.name === "dependencies" && (
          <section className="page-content" style={{ padding: "16px 24px" }}>
            <ServiceDependencyView
              initialSelectedService={view.selectedService}
              onOpenIncident={(id) => setView({ name: "detail", id })}
              onBack={() => setView({ name: "list" })}
            />
          </section>
        )}

        {/* ---------------- Report Form View ---------------- */}
        {view.name === "new" && (
          <section className="page-content">
            <div className="incident-intake-header">
              <button className="back-link" onClick={() => setView({ name: "list" })}>
                ← Back to Overview
              </button>
              <div className="eyebrow">INCIDENT INTAKE</div>
            </div>
            <h1>Report an Incident</h1>
            <p className="muted" style={{ marginBottom: "20px" }}>
              Capture the telemetry signal and let Tracewell assemble the causal graph.
            </p>
            <ReportForm
              onCreated={async (id, options) => {
                await refreshIncidents();
                setView({
                  name: "detail",
                  id,
                  autoInvestigate: Boolean(options?.autoInvestigate),
                });
              }}
            />
          </section>
        )}

        {/* ---------------- Incident Detail View ---------------- */}
        {view.name === "detail" && (
          <section className="page-content">
            <button className="back-link" onClick={() => setView({ name: "list" })}>
              ← Back to Overview
            </button>
            <ErrorBoundary
              title="Unable to Display Incident Detail"
              onBack={() => setView({ name: "list" })}
            >
              <IncidentDetail
                id={view.id}
                autoInvestigate={view.autoInvestigate}
                onBack={() => setView({ name: "list" })}
                onIncidentUpdated={refreshIncidents}
                onNavigateToDependencies={(serviceName) =>
                  setView({ name: "dependencies", selectedService: serviceName })
                }
              />
            </ErrorBoundary>
          </section>
        )}

        {/* Footer */}
        <footer className="footer">
          <span>
            TRACEWELL <span className="footer-dot">●</span> INCIDENT COPILOT
          </span>
          <span>Evidence first · Human approved · Automated triage</span>
        </footer>
      </main>
    </div>
  );
}

function Metric({ label, value, detail, tone }) {
  return (
    <article className="metric-card">
      <div className="metric-top">
        <span>{label}</span>
        <span className={`metric-icon-wrap ${tone}`}>
          <ActivityIcon size={14} />
        </span>
      </div>
      <div className="metric-value">{value}</div>
      <div className="metric-detail">
        <span className={`metric-dot ${tone}`} />
        {detail}
      </div>
    </article>
  );
}

function MetricSkeleton() {
  return (
    <article className="metric-card skeleton-metric-card" aria-hidden="true">
      <div className="metric-top">
        <span className="skeleton-line" style={{ width: "45%", height: "11px" }} />
        <span
          className="skeleton-line"
          style={{ width: "22px", height: "22px", borderRadius: "50%" }}
        />
      </div>
      <div
        className="skeleton-line"
        style={{ width: "32%", height: "28px", margin: "10px 0 8px 0" }}
      />
      <div className="skeleton-line" style={{ width: "65%", height: "11px" }} />
    </article>
  );
}

