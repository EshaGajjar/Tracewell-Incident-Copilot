import { useCallback, useEffect, useState } from "react";
import IncidentList from "./components/IncidentList.jsx";
import IncidentDetail from "./components/IncidentDetail.jsx";
import ReportForm from "./components/ReportForm.jsx";
import ServiceDependencyView from "./components/ServiceDependencyView.jsx";
import { API_URL, request } from "./firebase.js";

export default function App() {
  const [view, setView] = useState({ name: "list" });
  const [incidents, setIncidents] = useState([]);
  const [lastUpdated, setLastUpdated] = useState(null);
  const [incidentsLoading, setIncidentsLoading] = useState(true);
  const [incidentsError, setIncidentsError] = useState("");

  // One source of truth for incidents: fetch on startup and only when explicitly refreshed.
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

  const activeCount = incidents.filter((i) => !["closed", "resolved", "rejected"].includes(i.status)).length;
  const awaiting = incidents.filter((i) => i.status === "awaiting_approval").length;
  const critical = incidents.filter((i) => ["SEV1", "SEV2"].includes(i.severity) && !["closed", "resolved"].includes(i.status)).length;

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <button className="brand" onClick={() => setView({ name: "list" })} aria-label="Tracewell home">
          <span className="brand-mark">T<span>↗</span></span>
          <span><strong>tracewell</strong><small>INCIDENT INTELLIGENCE</small></span>
        </button>
        <div className="side-label">WORKSPACE</div>
        <button className={`nav-item ${view.name === "list" ? "selected" : ""}`} onClick={() => setView({ name: "list" })}><span>▦</span> Incident overview <span className="nav-count">{incidents.length}</span></button>
        <button className={`nav-item ${view.name === "dependencies" ? "selected" : ""}`} onClick={() => setView({ name: "dependencies" })}><span>☊</span> Service map</button>
        <button className={`nav-item ${view.name === "new" ? "selected" : ""}`} onClick={() => setView({ name: "new" })}><span>＋</span> Report incident</button>
        <div className="sidebar-bottom">
          <div className="system-indicator"><span className="pulse-dot" /><span><strong>Local environment</strong><small>{API_URL ? "API connected by URL" : "API URL not configured"}</small></span></div>
          <div className="sidebar-note">Evidence first. Human approved. Always.</div>
        </div>
      </aside>

      <main className="main-area">
        <header className="topbar">
          <div className="breadcrumbs">Workspace <span>/</span> <strong>{view.name === "new" ? "New incident" : view.name === "detail" ? "Incident detail" : view.name === "dependencies" ? "Service dependencies" : "Overview"}</strong></div>
          <div className="topbar-right"><span className="live-pill"><span className="pulse-dot" /> LIVE MONITORING</span><button className="primary-button compact" onClick={() => setView({ name: "new" })}>＋ Report incident</button></div>
        </header>

        {!API_URL && <div className="notice warning">Backend URL is missing. Set <code>VITE_API_URL=http://localhost:8000</code> in <code>frontend/.env</code>, then restart Vite.</div>}

        {view.name === "list" && <>
          <section className="welcome-row">
            <div><div className="eyebrow">OPERATIONS CENTER <span className="eyebrow-line" /></div><h1>Incident overview<span className="title-period">.</span></h1><p>Investigate faster. Make changes with confidence.</p></div>
            <div className="updated">◷ &nbsp;{lastUpdated ? `Updated ${lastUpdated.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}` : "Connecting to workspace…"}</div>
          </section>
          <section className="metrics-grid">
            <Metric label="TOTAL INCIDENTS" value={incidents.length} detail="All reported events" icon="▤" tone="blue" />
            <Metric label="ACTIVE INCIDENTS" value={activeCount} detail="Require attention" icon="◉" tone="violet" />
            <Metric label="AWAITING APPROVAL" value={awaiting} detail="Human decision needed" icon="⌁" tone="amber" />
            <Metric label="HIGH SEVERITY" value={critical} detail="SEV1 · SEV2 active" icon="↗" tone="rose" />
          </section>
          <section className="content-panel"><IncidentList items={incidents} loading={incidentsLoading} error={incidentsError} onRefresh={refreshIncidents} onOpen={(id) => setView({ name: "detail", id })} onCreate={() => setView({ name: "new" })} /></section>
        </>}
        {view.name === "dependencies" && (
          <section className="page-content">
            <ServiceDependencyView
              initialSelectedService={view.selectedService}
              onOpenIncident={(id) => setView({ name: "detail", id })}
            />
          </section>
        )}
        {view.name === "new" && <section className="page-content"><div className="eyebrow">INCIDENT INTAKE</div><h1>Report an incident<span className="title-period">.</span></h1><p className="page-lede">Capture the signal. Let Tracewell assemble the evidence.</p><ReportForm onCreated={async (id) => { await refreshIncidents(); setView({ name: "detail", id }); }} /></section>}
        {view.name === "detail" && (
          <section className="page-content">
            <button className="back-link" onClick={() => setView({ name: "list" })}>← Back to overview</button>
            <IncidentDetail
              id={view.id}
              onNavigateToDependencies={(serviceName) =>
                setView({ name: "dependencies", selectedService: serviceName })
              }
            />
          </section>
        )}

        <footer className="footer"><span>TRACEWELL <span className="footer-dot">●</span> INCIDENT COPILOT</span><span>AI-assisted analysis · Human-controlled action</span></footer>
      </main>
    </div>
  );
}

function Metric({ label, value, detail, icon, tone }) {
  return <article className="metric-card"><div className="metric-top"><span>{label}</span><span className={`metric-icon ${tone}`}>{icon}</span></div><div className="metric-value">{value}</div><div className="metric-detail"><span className={`metric-dot ${tone}`} />{detail}</div></article>;
}

